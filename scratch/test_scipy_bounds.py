import numpy as np
from scipy.optimize import minimize, NonlinearConstraint

def test_bounds():
    lower = 0.01
    upper = 0.1
    table_names = ('lineitem', 'orders')
    
    # Let's add a constraint: x[0] >= 1.5 (which is impossible because upper bound is 0.1)
    def constr_fn(x):
        return x[0]
        
    nonlin_constr = NonlinearConstraint(constr_fn, 1.5, np.inf)
    
    bounds_list = [(lower, upper) for _ in table_names]
    x0 = np.array([(lower + upper) / 2 for _ in table_names])
    
    sizes = np.array([59986052.0, 15000000.0])
    def obj(x):
        return float(np.dot(x, sizes))
        
    print("Running with SLSQP:")
    res = minimize(
        obj,
        x0,
        method="SLSQP",
        bounds=bounds_list,
        constraints=[nonlin_constr],
        options={"maxiter": 50}
    )
    print("Success:", res.success)
    print("Message:", res.message)
    print("x:", res.x)
    print("fun:", res.fun)

if __name__ == "__main__":
    test_bounds()
