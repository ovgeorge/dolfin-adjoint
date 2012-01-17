"""
Very stupid scheme for decoupled stationary Stokes + heat equation:

Given nu and f, find (u, p) such that

  (nu grad(u), grad(v)) + (p, div(v)) = (f, v)
                          (div(u), q) = 0

for all (v, q).

Given velocity u, find T such that

  (Dt(T), s) + (s*div(v) + (grad(T), grad(s)) = (1, s)

for all s

"""

import sys
from math import ceil

from dolfin import *

def stokes(W, nu, f):
    (u, p) = TrialFunctions(W)
    (v, q) = TestFunctions(W)
    a = (nu*inner(grad(u), grad(v)) +
         p*div(v) + q*div(u))*dx
    L = inner(f, v)*dx
    return (a, L)

def temperature(X, kappa, v, t_, k):
    t = TrialFunction(X)
    s = TestFunction(X)

    F = ((t - t_)/k*s + inner(kappa*grad(t), grad(s))
         + dot(v, grad(t))*s)*dx - s*dx
    (a, L) = system(F)
    return (a, L)

def flow_boundary_conditions(W):
    u0 = Constant((0.0,0.0))
    bottom = DirichletBC(W.sub(0), (0.0, 0.0), "near(x[1], 0.0)")
    top = DirichletBC(W.sub(0), (0.0, 0.0), "near(x[1], 1.0)")
    left = DirichletBC(W.sub(0).sub(0), 0.0, "near(x[0], 0.0)")
    right = DirichletBC(W.sub(0).sub(0), 0.0, "near(x[0], 1.0)")
    bcs = [bottom, top, left, right]
    return bcs

def temperature_boundary_conditions(Q):
    bc = DirichletBC(Q, 0.0, "near(x[1], 1.0)")
    return [bc]

n = 16
mesh = UnitSquare(n, n)
X = FunctionSpace(mesh, "CG", 1)

def main(ic, annotate=False):

    # Define meshes and function spaces
    V = VectorFunctionSpace(mesh, "CG", 2)
    Q = FunctionSpace(mesh, "CG", 1)
    W = V * Q

    # Define boundary conditions
    flow_bcs = flow_boundary_conditions(W)
    temp_bcs = temperature_boundary_conditions(X)

    # Temperature variables
    T_ = Function(ic)
    T = Function(ic)

    # Flow variable(s)
    w = Function(W)
    (u, p) = split(w)

    # Some parameters
    Ra = Constant(1.e4)
    nu = Constant(1.0)
    kappa = Constant(1.0)
    timestep = 0.1

    # Define flow equation
    g = as_vector((Ra*T_, 0))
    flow_eq = stokes(W, nu, g)

    # Define temperature equation
    temp_eq = temperature(X, kappa, u, T_, timestep)

    # Time loop
    t = 0.0
    end = 1.0

    if annotate:
      adjoint_checkpointing('multistage', int(ceil(end/float(timestep)))+2, 0, 5, verbose=True)
      print "Velocity: ", w
      print "Temperature: ", T

    while (t <= end):

        solve(flow_eq[0] == flow_eq[1], w, flow_bcs, annotate=annotate)

        solve(temp_eq[0] == temp_eq[1], T, temp_bcs, annotate=annotate)
        T_.assign(T, annotate=annotate)
        #plot(T)

        t += timestep

    return T_

if __name__ == "__main__":

    from dolfin_adjoint import *

    # Run model
    T0_expr = "0.5*(1.0 - x[1]*x[1]) + 0.01*cos(pi*x[0]/l)*sin(pi*x[1]/h)"
    T0 = Expression(T0_expr, l=1.0, h=1.0)
    ic = Function(interpolate(T0, X))
    T = main(ic, annotate=True)

    adj_html("stokes_forward.html", "forward")
    adj_html("stokes_adjoint.html", "adjoint")

    print "Running adjoint ... "
    adjoint = adjoint_dolfin(Functional(T*T*dx))

    def J(ic):
      T = main(ic, annotate=False)
      return assemble(T*T*dx)

    minconv = test_initial_condition_adjoint(J, ic, adjoint)
    if minconv < 1.9:
      exit_code = 1
    else:
      exit_code = 0
    sys.exit(exit_code)
