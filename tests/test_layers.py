from nngeometry.layers import Cosine1d, WeightNorm1d
import torch
from utils import check_ratio

def test_cosine():
    cosine_layer = Cosine1d(2, 3)

    # extract vector parallel to cosine_layer.weight[0, :]
    x_0 = cosine_layer.weight[0, :]
    x_col = 3 * x_0
    # extract vector orthogonal to cosine_layer.weight[1, :]
    x_1 = cosine_layer.weight[1, :]
    x_2 = cosine_layer.weight[2, :]
    x_orth = x_2 - (x_2 * x_1).sum() / torch.norm(x_1)**2 * x_1
    x_orth *= 1.5
    x = torch.stack((x_col, x_orth))

    out = cosine_layer(x)

    # this one is parallel hence cos(angle) = 1
    check_ratio(1, out[0, 0])
    # this one is orthogonal hence cos(angle) = 0
    check_ratio(0, out[1, 1])


def test_weightnorm1d():
    weightnorm_layer = WeightNorm1d(2, 3)
    
    mult0 = 3.3
    mult1 = 1.4

    # extract vector parallel to weightnorm_layer.weight[0, :]
    x_0 = weightnorm_layer.weight[0, :]
    x_col = mult0 * x_0
    # extract vector orthogonal to weightnorm_layer.weight[1, :]
    x_1 = weightnorm_layer.weight[1, :]
    x_2 = weightnorm_layer.weight[2, :]
    x_orth = x_2 - (x_2 * x_1).sum() / torch.norm(x_1)**2 * x_1
    x_orth *= mult1
    x = torch.stack((x_col, x_orth))

    out = weightnorm_layer(x)

    # this one is parallel hence cos(angle) = 1
    check_ratio(mult0 * torch.norm(x_0), out[0, 0])
    # this one is orthogonal hence cos(angle) = 0
    check_ratio(0, out[1, 1])
