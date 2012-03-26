import random

from dolfin import *
from dolfin_adjoint import *
import sys

mesh = UnitSquare(4, 4)
V = FunctionSpace(mesh, "CG", 3)
debugging["record_all"] = True
a = Constant(2.0)
b = Constant(3.0)

def main(ic, params, annotate=False):
  u = TrialFunction(V)
  v = TestFunction(V)
  (a, b) = params

  bc = DirichletBC(V, "-1.0", "on_boundary")

  mass = inner(u, v)*dx
  rhs = a*b*action(mass, ic)
  soln = Function(V)
  da = Function(V)

  solve(mass == rhs, soln, bc, annotate=annotate)
  return soln

if __name__ == "__main__":

  ic = project(Expression("x[0]*(x[0]-1)*x[1]*(x[1]-1)"), V)
  soln = main(ic, (a, b), annotate=True)

  J = FinalFunctional(soln*soln*dx)
  dJda = compute_gradient(J, ScalarParameters((a, b)))

  def J(params):
    soln = main(ic, params, annotate=False)
    return assemble(soln*soln*dx)

  minconv = test_scalar_parameters_adjoint(J, (a, b), dJda)

