#!/usr/bin/env python

# Copyright (C) 2011-2012 by Imperial College London
# Copyright (C) 2013 University of Oxford
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import copy

import dolfin
import ufl

from caches import *
from equation_solvers import *
from exceptions import *
from fenics_overrides import *
from fenics_utils import *
from pre_assembled_forms import *
from statics import *

__all__ = \
  [
    "PAEquationSolver",
    "pa_solve"
  ]

class PAEquationSolver(EquationSolver):
  """
  An EquationSolver applying additional pre-assembly and solver caching
  optimisations. This utilises pre-assembly of static terms. The arguments match
  those accepted by the DOLFIN solve function, with the following differences:

    Argument 1: May be a general equation. Linear systems are detected
      automatically.
    initial_guess: The initial guess for an iterative solver.
    adjoint_solver_parameters: A dictionary of solver parameters for an adjoint
      equation solve.
  """
  
  def __init__(self, *args, **kwargs):
    args, kwargs = copy.copy(args), copy.copy(kwargs)

    # Process arguments not to be passed to _extract_args
    if "parameters" in kwargs:
      parameters = kwargs["parameters"]
      del(kwargs["parameters"])
    else:
      parameters = dolfin.parameters["timestepping"]["pre_assembly"]
    if isinstance(parameters, dict):
      parameters = dolfin.Parameters(**parameters)
    else:
      parameters = dolfin.Parameters(parameters)
    if "initial_guess" in kwargs:
      if not kwargs["initial_guess"] is None and not isinstance(kwargs["initial_guess"], dolfin.Function):
        raise InvalidArgumentException("initial_guess must be a Function")
      initial_guess = kwargs["initial_guess"]
      del(kwargs["initial_guess"])
    else:
      initial_guess = None
    if "adjoint_solver_parameters" in kwargs:
      if not kwargs["adjoint_solver_parameters"] is None and not isinstance(kwargs["adjoint_solver_parameters"], dict):
        raise InvalidArgumentException("adjoint_solver_parameters must be a dictionary")
      adjoint_solver_parameters = kwargs["adjoint_solver_parameters"]
      del(kwargs["adjoint_solver_parameters"])
    else:
      adjoint_solver_parameters = None

    # Process remaining arguments
    if "form_compiler_parameters" in kwargs:
      raise NotImplementedException("form_compiler_parameters argument not supported")
    eq, x, bcs, J, tol, goal, form_parameters, solver_parameters = dolfin.fem.solving._extract_args(*args, **kwargs)

    # Relax requirements on equation syntax
    eq_lhs_rank = extract_form_data(eq.lhs).rank
    if eq_lhs_rank == 1:
      form = eq.lhs
      if not is_zero_rhs(eq.rhs):
        form -= eq.rhs
      if x in ufl.algorithms.extract_coefficients(form):
        if J is None:
          J = derivative(form, x)
        if x in ufl.algorithms.extract_coefficients(J):
          # Non-linear solve
          is_linear = False
        else:
          # Linear solve, rank 2 LHS
          cache_info("Detected that solve for %s is linear" % x.name(), dolfin.info_blue)
          form = replace(form, {x:dolfin.TrialFunction(x.function_space())})
          eq = lhs(form) == rhs(form)
          eq_lhs_rank = extract_form_data(eq.lhs).rank
          assert(eq_lhs_rank == 2)
          is_linear = True
      else:
        # Linear solve, rank 1 LHS
        is_linear = True
    elif eq_lhs_rank == 2:
      form = eq.lhs
      if not is_zero_rhs(eq.rhs):
        form -= eq.rhs
      if not x in ufl.algorithms.extract_coefficients(form):
        # Linear solve, rank 2 LHS
        eq = lhs(form) == rhs(form)
        eq_lhs_rank = extract_form_data(eq.lhs).rank
        assert(eq_lhs_rank == 2)
        is_linear = True
      else:
        # ??
        raise InvalidArgumentException("Invalid equation")
        
    # Initial guess sanity checking
    if not initial_guess is None:
      if is_linear and eq_lhs_rank == 1:
        # Supplied an initial guess for a linear solve with a rank 1 LHS -
        # ignore it
        initial_guess = None
      elif "linear_solver" in solver_parameters and not solver_parameters["linear_solver"] == "lu":
        # Supplied an initial guess with a Krylov solver - check the
        # initial_guess solver parameter
        if not "krylov_solver" in solver_parameters:
          solver_parameters["krylov_solver"] = {}
        if not "nonzero_initial_guess" in solver_parameters["krylov_solver"]:
          solver_parameters["krylov_solver"]["nonzero_initial_guess"] = True
        elif not solver_parameters["krylov_solver"]["nonzero_initial_guess"]:
          raise ParameterException("initial_guess cannot be set if nonzero_initial_guess solver parameter is False")
      elif is_linear:
        # Supplied an initial guess for a linear solve with an LU solver -
        # ignore it
        initial_guess = None

    # Initialise
    EquationSolver.__init__(self, eq, x, bcs, solver_parameters = solver_parameters, adjoint_solver_parameters = adjoint_solver_parameters)
    self.__args = args
    self.__kwargs = kwargs
    self.__J = J
    self.__tol = tol
    self.__goal = goal
    self.__form_parameters = form_parameters
    self.__initial_guess = initial_guess
    self.parameters = parameters

    # Assemble
    self.reassemble()

    return
  
  def reassemble(self, *args):
    """
    Reassemble the PAEquationSolver. If no arguments are supplied, reassemble
    both the LHS and RHS. Otherwise, only reassemble the LHS or RHS if they
    depend upon the supplied Constant s or Function s. Note that this does
    not clear the assembly or solver caches -- hence if a static Constant,
    Function, or DicichletBC is modified then one should clear the caches before
    calling reassemble on the PAEquationSolver.
    """
    
    x, eq, bcs, solver_parameters = self.x(), self.eq(), self.bcs(), self.solver_parameters()
    x_deps = self.dependencies()
    if self.is_linear():
      for dep in x_deps:
        if dep is x:
          raise DependencyException("Invalid non-linear solve")

      def assemble_lhs():
        eq_lhs_rank = extract_form_data(eq.lhs).rank
        if eq_lhs_rank == 2:
          static_bcs = n_non_static_bcs(bcs) == 0
          static_form = is_static_form(eq.lhs)
          if not self.parameters["equations"]["symmetric_boundary_conditions"] and len(bcs) > 0 and static_bcs and static_form:
            a = assembly_cache.assemble(eq.lhs, bcs = bcs, symmetric_bcs = False)
            cache_info("Pre-assembled LHS terms in solve for %s    : 1" % x.name(), dolfin.info_blue)
            cache_info("Non-pre-assembled LHS terms in solve for %s: 0" % x.name(), dolfin.info_blue)
            solver = solver_cache.solver(eq.lhs, solver_parameters, static = True, bcs = bcs, symmetric_bcs = False)
          else:
            a = PABilinearForm(eq.lhs, parameters = self.parameters["bilinear_forms"])
            cache_info("Pre-assembled LHS terms in solve for %s    : %i" % (x.name(), a.n_pre_assembled()), dolfin.info_blue)
            cache_info("Non-pre-assembled LHS terms in solve for %s: %i" % (x.name(), a.n_non_pre_assembled()), dolfin.info_blue)
            solver = solver_cache.solver(eq.lhs, solver_parameters, static = a.is_static() and static_bcs, bcs = bcs, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])
        else:
          assert(eq_lhs_rank == 1)
          a = PALinearForm(eq.lhs, parameters = self.parameters["linear_forms"])
          cache_info("Pre-assembled LHS terms in solve for %s    : %i" % (x.name(), a.n_pre_assembled()), dolfin.info_blue)
          cache_info("Non-pre-assembled LHS terms in solve for %s: %i" % (x.name(), a.n_non_pre_assembled()), dolfin.info_blue)
          solver = None
        return a, solver
      def assemble_rhs():
        L = PALinearForm(eq.rhs, parameters = self.parameters["linear_forms"])
        cache_info("Pre-assembled RHS terms in solve for %s    : %i" % (x.name(), L.n_pre_assembled()), dolfin.info_blue)
        cache_info("Non-pre-assembled RHS terms in solve for %s: %i" % (x.name(), L.n_non_pre_assembled()), dolfin.info_blue)
        return L

      if len(args) == 0:
        a, solver = assemble_lhs()
        L = assemble_rhs()
      else:
        a, solver = self.__a, self.__solver
        L = self.__L
        lhs_cs = ufl.algorithms.extract_coefficients(eq.lhs)
        rhs_cs = ufl.algorithms.extract_coefficients(eq.rhs)
        for dep in args:
          if dep in lhs_cs:
            a, solver = assemble_lhs()
            break
        for dep in args:
          if dep in rhs_cs:
            L = assemble_rhs()
            break
    else:
      J, hbcs = self.J(), self.hbcs()

      def assemble_lhs():
        a = PABilinearForm(J, parameters = self.parameters["bilinear_forms"])
        cache_info("Pre-assembled LHS terms in solve for %s    : %i" % (x.name(), a.n_pre_assembled()), dolfin.info_blue)
        cache_info("Non-pre-assembled LHS terms in solve for %s: %i" % (x.name(), a.n_non_pre_assembled()), dolfin.info_blue)
        solver = solver_cache.solver(J, solver_parameters, static = False, bcs = hbcs, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])
        return a, solver
      def assemble_rhs():
        L = -eq.lhs
        if not is_zero_rhs(eq.rhs):
          L += eq.rhs
        L = PALinearForm(L, parameters = self.parameters["linear_forms"])
        cache_info("Pre-assembled RHS terms in solve for %s    : %i" % (x.name(), L.n_pre_assembled()), dolfin.info_blue)
        cache_info("Non-pre-assembled RHS terms in solve for %s: %i" % (x.name(), L.n_non_pre_assembled()), dolfin.info_blue)
        return L

      if len(args) == 0:
        a, solver = assemble_lhs()
        L = assemble_rhs()
      else:
        a, solver = self.__a, self.__solver
        L = self.__L
        lhs_cs = ufl.algorithms.extract_coefficients(J)
        rhs_cs = ufl.algorithms.extract_coefficients(eq.lhs)
        if not is_zero_rhs(eq.rhs):
          rhs_cs += ufl.algorithms.extract_coefficients(eq.rhs)
        for dep in args:
          if dep in lhs_cs:
            a, solver = assemble_lhs()
            break
        for dep in args:
          if dep in rhs_cs:
            L = assemble_rhs()
            break
       
      self.__dx = x.vector().copy()

    self.__a, self.__solver = a, solver
    self.__L = L

    return

  def dependencies(self, non_symbolic = False):
    """
    Return equation dependencies. If non_symbolic is true, also return any
    other dependencies which could alter the result of a solve, such as the
    initial guess.
    """
    
    def uses_x_as_initial_guess():
      if not self.is_linear():
        return self.__initial_guess is None
      solver = self.solver()
      if solver is None:
        return False
      else:
        return self.__initial_guess is None and hasattr(solver.parameters, "nonzero_initial_guess") and solver.parameters["nonzero_initial_guess"]
    
    if not non_symbolic:
      return EquationSolver.dependencies(self, non_symbolic = False)
    elif not self.__initial_guess is None:
      return EquationSolver.dependencies(self, non_symbolic = True) + [self.__initial_guess]
    elif uses_x_as_initial_guess():
      return EquationSolver.dependencies(self, non_symbolic = True) + [self.x()]
    else:
      return EquationSolver.dependencies(self, non_symbolic = True)

  def solver(self):
    """
    Return the linear solver.
    """
    
    return self.__solver

  def solve(self):
    """
    Solve the equation. This utilises a custom Newton solver for non-linear
    equations. The Newton solver is intended to have near identical behaviour
    to the Newton solver supplied with DOLFIN, but utilises pre-assembly.
    """
    
    x = self.x()
    if not self.__initial_guess is None:
      x.assign(self.__initial_guess)
    
    if self.is_linear():
      bcs, solver = self.bcs(), self.solver()

      if isinstance(self.__a, dolfin.GenericMatrix):
        a = self.__a
        L = assemble(self.__L, copy = len(bcs) > 0)
        enforce_bcs(L, bcs)

        solver.set_operator(a)
        solver.solve(x.vector(), L)
      elif self.__a.rank() == 2:
        a = assemble(self.__a, copy = len(bcs) > 0)
        L = assemble(self.__L, copy = len(bcs) > 0)
        apply_bcs(a, bcs, L = L, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])

        solver.set_operator(a)
        solver.solve(x.vector(), L)
      else:
        assert(self.__a.rank() == 1)
        assert(solver is None)
        a = assemble(self.__a, copy = False)
        L = assemble(self.__L, copy = False)

        x.vector().set_local(L.array() / a.array())
        x.vector().apply("insert")
        enforce_bcs(x.vector(), bcs)
    else:
      # Newton solver, intended to have near identical behaviour to the Newton
      # solver supplied with DOLFIN. See
      # http://fenicsproject.org/documentation/tutorial/nonlinear.html for
      # further details.
      
      default_parameters = dolfin.NewtonSolver.default_parameters()
      solver_parameters = self.solver_parameters()
      if "newton_solver" in solver_parameters:
        parameters = solver_parameters["newton_solver"]
      else:
        parameters = {}
      solver = self.solver()

      atol = default_parameters["absolute_tolerance"]
      rtol = default_parameters["relative_tolerance"]
      max_its = default_parameters["maximum_iterations"]
      omega = default_parameters["relaxation_parameter"]
      err = default_parameters["error_on_nonconvergence"]
      r_def = default_parameters["convergence_criterion"]
      for key in parameters.keys():
        if key == "absolute_tolerance":
          atol = parameters[key]
        elif key == "convergence_criterion":
          r_def = parameters[key]
        elif key == "error_on_nonconvergence":
          err = parameters[key]
        elif key == "maximum_iterations":
          max_its = parameters[key]
        elif key == "relative_tolerance":
          rtol = parameters[key]
        elif key == "relaxation_parameter":
          omega = parameters[key]
        elif key in ["method", "report"]:
          raise NotImplementedException("Unsupported solver parameter: %s" % key)
        else:
          raise InvalidArgumentException("Unexpected solver parameter: %s" % key)

      eq, bcs, hbcs = self.eq(), self.bcs(), self.hbcs()
      a, L = self.__a, self.__L

      x_name = x.name()
      x = x.vector()
      enforce_bcs(x, bcs)

      dx = self.__dx
      if not isinstance(solver, dolfin.GenericLUSolver):
        dx.zero()
        
      if r_def == "residual":
        l_L = assemble(L, copy = len(hbcs) > 0)
        enforce_bcs(l_L, hbcs)
        r_0 = l_L.norm("l2")
        it = 0
        if r_0 >= atol:
          l_a = assemble(a, copy = len(hbcs) > 0)
          apply_bcs(l_a, hbcs, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])
          solver.set_operator(l_a)
          solver.solve(dx, l_L)
          x.axpy(omega, dx)
          it += 1
          atol = max(atol, r_0 * rtol)
          while it < max_its:
            l_L = assemble(L, copy = len(hbcs) > 0)
            enforce_bcs(l_L, hbcs)
            r = l_L.norm("l2")
            if r < atol:
              break
            l_a = assemble(a, copy = len(hbcs) > 0)
            apply_bcs(l_a, hbcs, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])
            solver.set_operator(l_a)
            solver.solve(dx, l_L)
            x.axpy(omega, dx)
            it += 1
      elif r_def == "incremental":
        l_a = assemble(a, copy = len(hbcs) > 0)
        l_L = assemble(L, copy = len(hbcs) > 0)
        apply_bcs(l_a, hbcs, L = l_L, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])
        solver.set_operator(l_a)
        solver.solve(dx, l_L)
        x.axpy(omega, dx)
        it = 1
        r_0 = dx.norm("l2")
        if r_0 >= atol:
          atol = max(atol, rtol * r_0)
          while it < max_its:
            l_a = assemble(a, copy = len(hbcs) > 0)
            l_L = assemble(L, copy = len(hbcs) > 0)
            apply_bcs(l_a, hbcs, L = l_L, symmetric_bcs = self.parameters["equations"]["symmetric_boundary_conditions"])
            solver.set_operator(l_a)
            solver.solve(dx, l_L)
            x.axpy(omega, dx)
            it += 1
            if dx.norm("l2") < atol:
              break
      else:
        raise ParameterException("Invalid convergence criterion: %s" % r_def)
      if it == max_its:
        if err:
          raise StateException("Newton solve for %s failed to converge after %i iterations" % (x_name, it))
        else:
          dolfin.info_red("Warning: Newton solve for %s failed to converge after %i iterations" % (x_name, it))
#      dolfin.info("Newton solve for %s converged after %i iterations" % (x_name, it))

    return

def pa_solve(*args, **kwargs):
  """
  Instantiate a PAEquationSolver using the supplied arguments and call its solve
  method.
  """
  
  PAEquationSolver(*args, **kwargs).solve()

  return