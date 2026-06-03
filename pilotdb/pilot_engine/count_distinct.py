import math
import logging

logger = logging.getLogger(__name__)

def chao_estimator(d: float, f1: float, f2: float) -> float:
    """Chao's estimator for distinct counts (for low-duplicity/high-variance samples).
    
    Formula: D = d + (f1^2) / (2 * f2)
    """
    if f2 <= 0:
        return d
    return d + (f1 ** 2) / (2.0 * f2)

def gee_estimator(d: float, f1: float, p: float) -> float:
    """Generalized Jackknife (GEE) / Horvitz-Thompson estimator for distinct counts.
    
    Used when f2 == 0.
    Formula: D = d + f1 * sqrt(1/p) * (1 - p)
    """
    if p <= 0 or p >= 1:
        return d
    return d + f1 * math.sqrt(1.0 / p) * (1.0 - p)

def estimate_distinct(d: float, f1: float, f2: float, p: float, N: float = None) -> float:
    """Dispatches to Chao's or GEE estimator depending on the doubleton count (f2).
    
    Bounds the estimate below by the observed distinct count (d).
    Bounds the estimate above by the total row count (N) if provided.
    """
    if d <= 0:
        return 0.0
        
    if f2 > 0:
        est = chao_estimator(d, f1, f2)
        logger.debug("Chao estimate: d=%s f1=%s f2=%s p=%s -> %s", d, f1, f2, p, est)
    else:
        est = gee_estimator(d, f1, p)
        logger.debug("GEE estimate: d=%s f1=%s p=%s -> %s", d, f1, p, est)
        
    est = max(d, est)
    if N is not None:
        est = min(N, est)
        
    return est
