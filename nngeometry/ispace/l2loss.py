import torch

class L2Loss:
    def __init__(self, model, dataloader, loss_closure):
        self.model = model
        self.dataloader = dataloader
        self.handles = []
        self.x_outer = dict()
        self.x_inner = dict()
        self.xs = dict()
        self.gy_outer = dict()
        self.p_pos = dict() # maps parameters to their position in flattened representation
        self.mods = self._get_individual_modules(model)
        self.loss_closure = loss_closure

    def release_buffers(self):
        self.x_outer = dict()
        self.x_inner = dict()
        self.xs = dict()
        self.gy_outer = dict()

    def get_matrix(self):
        # add hooks
        self.handles += self._add_hooks(self._hook_savex_io, self._hook_compute_flat_grad)

        device = next(self.model.parameters()).device
        n_examples = len(self.dataloader.sampler)
        n_parameters = sum([p.numel() for p in self.model.parameters()])
        bs = self.dataloader.batch_size
        self.G = torch.zeros((n_examples, n_examples), device=device)
        self.e_outer = 0
        for i_outer, (inputs, targets) in enumerate(self.dataloader):
            self.outerloop_switch = True # used in hooks to switch between store/compute
            inputs, targets = inputs.to(device), targets.to(device)
            bs_outer = targets.size(0)
            inputs.requires_grad = True
            loss = self.loss_closure(inputs, targets)
            torch.autograd.grad(loss, [inputs])
            self.outerloop_switch = False 

            self.e_inner = 0
            for i_inner, (inputs, targets) in enumerate(self.dataloader):
                if i_inner > i_outer:
                    break
                inputs, targets = inputs.to(device), targets.to(device)
                inputs.requires_grad = True
                loss = self.loss_closure(inputs, targets)
                torch.autograd.grad(loss, [inputs])
                if i_inner < i_outer: # exclude diagonal
                    bs_inner = targets.size(0)
                    self.G[self.e_outer:self.e_outer+bs_outer, self.e_inner:self.e_inner+bs_inner] += \
                        self.G[self.e_inner:self.e_inner+bs_inner, self.e_outer:self.e_outer+bs_outer].t()
                self.e_inner += inputs.size(0)

            self.e_outer += inputs.size(0)
        G = self.G

        # remove hooks
        del self.e_inner, self.e_outer
        del self.G
        self.x_inner = dict()
        self.x_outer = dict()
        for h in self.handles:
            h.remove()

        return G

    def implicit_m_norm(self, v):
        # add hooks
        self.handles += self._add_hooks(self._hook_savex, self._hook_compute_m_norm)

        device = next(self.model.parameters()).device
        n_examples = len(self.dataloader.sampler)
        n_parameters = sum([p.numel() for p in self.model.parameters()])
        bs = self.dataloader.batch_size

        self._cTv = torch.zeros((n_parameters,), device=device)
        i = 0
        for i_outer, (inputs, targets) in enumerate(self.dataloader):
            self._c = v[i:i+bs]
            i += bs
            inputs, targets = inputs.to(device), targets.to(device)
            inputs.requires_grad = True
            loss = self.loss_closure(inputs, targets)
            torch.autograd.grad(loss, [inputs])
        m_norm = (self._cTv**2).sum()**.5

        # remove hooks
        del self._cTv
        self.xs = dict()
        for h in self.handles:
            h.remove()

        return m_norm

    def _get_individual_modules(self, model):
        mods = []
        sizes_mods = []
        parameters = []
        start = 0
        for mod in model.modules():
            mod_class = mod.__class__.__name__
            if mod_class in ['Linear', 'Conv2d']:
                mods.append(mod)
                self.p_pos[mod] = start
                sizes_mods.append(mod.weight.size())
                parameters.append(mod.weight)
                start += mod.weight.numel()
                if mod.bias is not None:
                    sizes_mods.append(mod.bias.size())
                    parameters.append(mod.bias)
                    start += mod.bias.numel()

        # check order of flattening
        sizes_flat = [p.size() for p in model.parameters() if p.requires_grad]
        assert sizes_mods == sizes_flat
        # check that all parameters were added
        # will fail if using exotic layers such as BatchNorm
        assert len(set(parameters) - set(model.parameters())) == 0
        return mods

    def _add_hooks(self, hook_x, hook_gy):
        handles = []
        for m in self.mods:
            handles.append(m.register_forward_pre_hook(hook_x))
            handles.append(m.register_backward_hook(hook_gy))
        return handles

    def _hook_savex_io(self, mod, i):
        if self.outerloop_switch:
            self.x_outer[mod] = i[0]
        else:
            self.x_inner[mod] = i[0]

    def _hook_savex(self, mod, i):
        self.xs[mod] = i[0]

    def _hook_compute_flat_grad(self, mod, grad_input, grad_output):
        if self.outerloop_switch:
            self.gy_outer[mod] = grad_output[0]
        else:
            mod_class = mod.__class__.__name__
            gy_inner = grad_output[0]
            gy_outer = self.gy_outer[mod]
            x_outer = self.x_outer[mod]
            x_inner = self.x_inner[mod]
            bs_inner = x_inner.size(0)
            bs_outer = x_outer.size(0)
            start = self.p_pos[mod]
            if mod_class == 'Linear':
                self.G[self.e_inner:self.e_inner+bs_inner, self.e_outer:self.e_outer+bs_outer] += \
                        torch.mm(x_inner, x_outer.t()) * torch.mm(gy_inner, gy_outer.t())
                if mod.bias is not None:
                    self.G[self.e_inner:self.e_inner+bs_inner, self.e_outer:self.e_outer+bs_outer] += \
                            torch.mm(gy_inner, gy_outer.t())
            else:
                raise NotImplementedError

    def _hook_compute_m_norm(self, mod, grad_input, grad_output):
        mod_class = mod.__class__.__name__
        gy = grad_output[0]
        xs = self.xs[mod]
        start = self.p_pos[mod]
        if mod_class == 'Linear':
            self._cTv[start:start+mod.weight.numel()] += torch.mm(gy.t(), self._c.view(-1, 1) * xs).view(-1)
            if mod.bias is not None:
                self._cTv[start+mod.weight.numel():start+mod.weight.numel()+mod.bias.numel()] += \
                    torch.mv(gy.t(), self._c)
        else:
            raise NotImplementedError
