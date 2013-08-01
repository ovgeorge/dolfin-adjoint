""" 
Test for the mapping to Euclidian space in the ReducedFunctionalNumPy module.
"""
import sys
import numpy as np
from dolfin import *
from dolfin_adjoint import *

def solve_pde(u, V, m):
    v = TestFunction(V)
    F = ((u-m)*v)*dx 
    solve(F == 0, u)

# Solves the linear optimisation problem 
n = 10
mesh = Mesh("mesh.xml")
V = FunctionSpace(mesh, "CG", 1)

m = project(Constant(5), V, name='Control')
u = Function(V, name='State')

J = Functional(0.5*u*u*dx)

# Run the forward model once to create the annotation
solve_pde(u, V, m)

# Construct the reduced functionals
p = SteadyParameter(m, value=m) 

rf = ReducedFunctional(J, p)
rf_numpy = ReducedFunctionalNumPy(rf)
rf_numpy_euc = ReducedFunctionalNumPy(rf, map_to_euclidian_space=True)

ma = m.vector().array()
ma_eucl = np.dot(rf_numpy_euc.LT, ma)

# Test equivalence of norms
assert abs(assemble(m*m*dx) - 25) < 1e-12
assert abs(np.dot(ma_eucl, ma_eucl) - 25) < 1e-12 

# Test equivalence of functionals
j = rf_numpy(ma)
j_eucl = rf_numpy_euc(ma_eucl)

assert abs(j - 12.5) < 1e-12
assert abs(j_eucl - 12.5) < 1e-12

# Test equivalence of gradients
dj = rf.derivative(project=True, forget=False)[0]
dj_eucl = rf_numpy_euc.derivative(ma_eucl, project=True, forget=False)

s = project(Expression("sin(x[0])"), V, annotate=False)
sa = s.vector().array()
s_eucl = np.dot(rf_numpy_euc.LT, sa)

djs = assemble(inner(dj, s)*dx)
djs_eucl = np.dot(dj_eucl, s_eucl)

assert djs - djs_eucl < 1e-12

# Test equivalence of gradients with project = False
dj = rf.derivative(project=False, forget=False)[0]
dj_eucl = rf_numpy_euc.derivative(ma_eucl, project=False, forget=False)

djs = dj.vector().inner(s.vector())
djs_eucl = np.dot(dj_eucl, s_eucl)

assert djs - djs_eucl < 1e-12

# Test the equivalence of Hessians
m_dot = project(Expression("exp(x[0])"), V, annotate=False)
m_dota = m_dot.vector().array()
m_dot_eucl = np.dot(rf_numpy_euc.LT, m_dota)

H = rf.hessian(m_dot)[0]
H_eucl = rf_numpy_euc.hessian(ma_eucl, m_dot_eucl)

Hs = H.vector().inner(s.vector())
Hs_eucl = np.dot(H_eucl, s_eucl)

assert Hs - Hs_eucl < 1e-12

info_green("Test passed")
