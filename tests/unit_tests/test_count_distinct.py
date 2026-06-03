import pytest
import math
from pilotdb.pilot_engine.count_distinct import chao_estimator, gee_estimator, estimate_distinct

def test_chao_estimator():
    # Chao formula: D = d + f1^2 / (2 * f2)
    # If d=100, f1=10, f2=5: D = 100 + 100 / 10 = 110
    assert chao_estimator(100, 10, 5) == 110.0
    
    # If f2 <= 0, should return d
    assert chao_estimator(100, 10, 0) == 100.0
    assert chao_estimator(100, 10, -1) == 100.0

def test_gee_estimator():
    # GEE formula: D = d + f1 * sqrt(1/p) * (1 - p)
    # If d=100, f1=10, p=0.01 (1%): D = 100 + 10 * 10 * 0.99 = 199
    assert math.isclose(gee_estimator(100, 10, 0.01), 199.0)
    
    # If p <= 0 or p >= 1, should return d
    assert gee_estimator(100, 10, 0.0) == 100.0
    assert gee_estimator(100, 10, 1.0) == 100.0
    assert gee_estimator(100, 10, -0.5) == 100.0
    assert gee_estimator(100, 10, 1.5) == 100.0

def test_estimate_distinct():
    # Dispatch to Chao when f2 > 0
    # d=100, f1=10, f2=5, p=0.01. Estimate should be 110.0
    assert estimate_distinct(100, 10, 5, 0.01) == 110.0
    
    # Dispatch to GEE when f2 == 0
    # d=100, f1=10, f2=0, p=0.01. Estimate should be 199.0
    assert math.isclose(estimate_distinct(100, 10, 0, 0.01), 199.0)
    
    # Bound below by d: if estimate is less than d (not possible mathematically unless weird values, but tested for safety)
    # d=100, f1=0, f2=0, p=0.01 -> est=100
    assert estimate_distinct(100, 0, 0, 0.01) == 100.0
    
    # Bound above by N: if N is 150, estimate of 199.0 should be capped at 150
    assert estimate_distinct(100, 10, 0, 0.01, N=150) == 150.0
    
    # If d <= 0, return 0.0
    assert estimate_distinct(0, 10, 5, 0.01) == 0.0
