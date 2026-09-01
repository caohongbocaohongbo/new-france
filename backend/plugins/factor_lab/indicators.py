"""06 因子实验室统计纯函数（pandas/numpy，不依赖 scipy）。"""
import math

import pandas as pd


def rank_ic(factor_values, forward_returns) -> float:
    """Rank IC = spearman(因子值, 前向收益)。用 pandas rank + numpy 现算，不依赖 scipy。"""
    import numpy as np

    f = pd.Series(list(factor_values or []), dtype="float64")
    r = pd.Series(list(forward_returns or []), dtype="float64")
    mask = f.notna() & r.notna()
    f, r = f[mask], r[mask]
    if len(f) < 3 or f.nunique() < 2 or r.nunique() < 2:
        return None
    rf = f.rank(method="average").values
    rr = r.rank(method="average").values
    ic = float(np.corrcoef(rf, rr)[0, 1])
    return None if math.isnan(ic) else round(ic, 4)


def ic_ir(ics: list) -> dict:
    """IR = mean(IC) / std(IC)。"""
    vals = [float(v) for v in ics or [] if v is not None]
    if not vals:
        return {"mean": None, "std": None, "ir": None, "t_stat": None}
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
    ir = mean / std if std > 0 else None
    t_stat = mean / (std / math.sqrt(len(vals))) if std > 0 else None
    return {"mean": round(mean, 4), "std": round(std, 4), "ir": round(ir, 4) if ir is not None else None, "t_stat": round(t_stat, 4) if t_stat is not None else None}


def layer_returns(factor_values, forward_returns, n_layers: int = 5) -> list:
    """按因子值分 5 层，各组平均前向收益。"""
    f = pd.Series(list(factor_values or []), dtype="float64")
    r = pd.Series(list(forward_returns or []), dtype="float64")
    mask = f.notna() & r.notna()
    f, r = f[mask], r[mask]
    if f.empty or f.nunique() < 2:
        return []
    try:
        labels = pd.qcut(f, q=int(n_layers), labels=False, duplicates="drop")
    except ValueError:
        return []
    df = pd.DataFrame({"layer": labels, "ret": r})
    result = []
    for layer, group in df.groupby("layer"):
        result.append({"layer": int(layer) + 1, "count": int(len(group)), "avg_return": round(float(group["ret"].mean()), 4)})
    return result


def build_factor_stats(panel: pd.DataFrame) -> list:
    """由因子面板计算每个因子的 IC/IR/分层。panel: [factor_name, factor_value, forward_ret_t1]。"""
    if panel is None or panel.empty:
        return []
    result = []
    for factor, group in panel.groupby("factor_name"):
        values = group["factor_value"].tolist()
        rets = group["forward_ret_t1"].tolist()
        result.append({
            "factor": str(factor),
            "sample_count": int(len(group)),
            "ic": rank_ic(values, rets),
            "ir": ic_ir([rank_ic(values, rets)]).get("ir") if rank_ic(values, rets) is not None else None,
            "layers": layer_returns(values, rets),
        })
    return result
