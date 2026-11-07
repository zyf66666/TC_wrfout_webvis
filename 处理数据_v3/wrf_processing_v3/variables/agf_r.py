"""agf_r：超梯度力（径向非梯度风合力）= pgf_r + cen_r + cor_r + motf_r（m s-2，正 = 超梯度、向外）。算法见 _agradient.py。"""

import numpy as np

from ._helpers import save


def process(ctx):
    terms = {name: np.asarray(ctx.read(name), dtype=float) for name in ("pgf_r", "cen_r", "cor_r", "motf_r")}
    shape = terms["pgf_r"].shape
    if any(value.shape != shape for value in terms.values()):
        raise ValueError("超梯度力各项形状必须一致")
    values = terms["pgf_r"] + terms["cen_r"] + terms["cor_r"] + terms["motf_r"]
    meta = ctx.read_metadata("cen_r")
    save(ctx, values.astype(np.float32), units="m s-2", formula="pgf_r + cen_r + cor_r + motf_r",
         derived_from=["pgf_r", "cen_r", "cor_r", "motf_r"],
         **{key: meta[key] for key in ("center_mode", "center_yx", "coriolis_f", "storm_motion_u_v_m_s",
                                       "motion_half_window_h", "sign") if key in meta})
