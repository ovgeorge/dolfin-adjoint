import random

from dolfin import *
from dolfin_adjoint import *
import sys

mesh = UnitSquare(4, 4)
V = FunctionSpace(mesh, "CG", 1)
debugging["record_all"] = True

def main(ic, annotate=False):
  u = TrialFunction(V)
  v = TestFunction(V)

  bc = DirichletBC(V, "-1.0", "on_boundary")

  mass = inner(u, v)*dx
  soln = Function(V)

  solve(mass == action(mass, ic), soln, bc, annotate=annotate)
  return soln

def propagator(ic): # a hand-coded tangent linear model, essentially
  u = TrialFunction(V)
  v = TestFunction(V)

  bc = DirichletBC(V, "0.0", "on_boundary")

  mass = inner(u, v)*dx
  soln = Function(V)

  solve(mass == action(mass, ic), soln, bc, annotate=annotate)
  return soln

if __name__ == "__main__":

  ic = project(Expression("x[0]*(x[0]-1)*x[1]*(x[1]-1)"), V)
  soln = main(ic, annotate=True)

  svd = adj_compute_tlm_svd(ic, soln, 1)
  (sigma, u, v, error) = svd.get_svd(0, return_vectors=True, return_error=True)

  print "Maximal singular value: ", sigma

  Lv = propagator(v.data)
  residual = (Lv.vector() - sigma*u.data.vector()).norm("l2")
  print "Residual: ", residual

  assert residual < 1.0e-14