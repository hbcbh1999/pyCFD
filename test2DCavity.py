"""
2D laminar cavity flow test case
"""
__author__ = "Bence Somogyi"
__copyright__ = "Copyright 2014"
__version__ = "0.1"
__maintainer__ = "Bence Somogyi"
__email__ = "bencesomogyi@ivt.tugraz.at"
__status__ = "Prototype"

import time
t00 = time.time()

import numpy
import pyCFD_mesh.readers as readers
import pyCFD_output.output as output
import pyCFD_fields.fields as fields
import pyCFD_fields.calculated_fields as calcfield
import pyCFD_calculation.time_loop as time_loop
import pyCFD_operators.generic_equation as generic_equation
import pyCFD_operators.explicit_operators as explicit_operators
import pyCFD_operators.implicit_operators as implicit_operators

# clear output dirs
output.clean_output_dirs()

# create mesh object and read mesh data from MESHES directory
myMesh = readers.FoamMesh("cavity")
cell_volumes = myMesh.get_volumes()[:,0]
face_areas = myMesh.get_areas()

# create and initialize field U, V and W
U = fields.VectorField(myMesh, "U")
U.get_patch("movingWall"  ).set_patch_uniform([1.,0.,0.], "fixedValue"   )
U.get_patch("frontAndBack").set_patch_uniform(0., "fixedGradient")
U_old = fields.VectorField(myMesh, "U_old")

# create and initialize field p
p = fields.ScalarField(myMesh, "p", 0.)
p.get_patch("movingWall"  ).set_patch_uniform(0., "fixedGradient")
p.get_patch("fixedWalls"  ).set_patch_uniform(0., "fixedGradient")
p.get_patch("frontAndBack").set_patch_uniform(0., "fixedGradient")

# create and initialize field p_corr
p_corr = fields.ScalarField(myMesh, "p_corr", 0.)
p_corr.get_patch("movingWall"  ).set_patch_uniform(0., "fixedGradient")
p_corr.get_patch("fixedWalls"  ).set_patch_uniform(0., "fixedGradient")
p_corr.get_patch("frontAndBack").set_patch_uniform(0., "fixedGradient")

# create rho field
rho = fields.ScalarField(myMesh, "rho", 1.)
one_over_rho = fields.ScalarField(myMesh, "1_rho", 1.)

# create massflux field
m_dot = calcfield.MassFlux(U, rho)
m_dot_old = fields.SurfaceScalarField(myMesh, "massFlux_old", )

# set constant dyn viscosity
Re = 10
nu = 1. * 0.1 / Re

# create time loop
save_fields = [U, p, p_corr]

start_time = 0.
stop_time  = 0.5
time_step  = 0.005
save_step  = 0.05

myLoop = time_loop.TimeLoop(save_fields, start_time, stop_time, time_step)
myLoop.uniform_save_times(save_step)

# set up equation for U and p_corr
U_eqn      = generic_equation.GenericVectorEquation(myMesh, U,      "bicg",        0.7)
p_corr_eqn = generic_equation.GenericScalarEquation(myMesh, p_corr, "lu_solver"  , 1.0)
p_under_relaxation = 0.3

# save initial condition
if start_time == 0.:
    # clear output dirs
    output.clean_output_dirs()
    myLoop.save_current(0)

# prepare monitors
# linear solver residuals are added by default
# average pressure
p_corr_eqn.add_monitor("p_average")
# divergence
p_corr_eqn.add_monitor("divU_pred")
p_corr_eqn.add_monitor("divU_corr")
if start_time == 0.:
    U_eqn.save_residual_header("U")
    p_corr_eqn.save_residual_header("p")

# build Laplace term coefficient matrix now as it will not change
U_lapl = implicit_operators.LaplaceVec(U, nu)
p_lapl = implicit_operators.Laplace(p_corr)
p_lapl.fix_cell_value(0)

# iteration loop
for time_ in myLoop.times:
    myLoop.time = time_
    myLoop.print_time()
    
    # update boundary conditions, reset matrices and right hand sides, update m_dot   
    U_eqn.reset()
    p_corr_eqn.reset()
    p.update_boundary_values()
    m_dot = calcfield.MassFlux(U, rho)
    
    ## PREDICTOR EULER
    # assemble momentum equation
    U_eqn += implicit_operators.DdtEulerVec(U_eqn, myLoop.dt)
    Dt = fields.ScalarField(myMesh, "Dt_time")
    Dt.initialize_cell_with_vector(U_eqn.diag())
    U_eqn += implicit_operators.DivergenceVec(U, m_dot, "MINMOD")
    # use pre-calculated coefficient matrix for the laplace operator
    U_eqn -= implicit_operators.LaplaceVec(U, nu, "", False)
    U_eqn.A = U_eqn.A - U_lapl.A
    U_eqn += explicit_operators.Gradient(p, one_over_rho)
    
    # solve momentum equation
    U_eqn.solve()
    
    # calculate predicted massflux field
    m_dot = calcfield.MassFlux(U, rho)
    
    # monitor predictor divergence
    div = calcfield.Divergence(None, m_dot, "")
    print "predictor divergence: "+str(max(abs(div.V)))
    p_corr_eqn.append_to_monitor(max(abs(div.V)), "divU_pred")
    
    ## CORRECTOR
    # correct m_dot with Rie-Chow interpolation
    D = fields.ScalarField(myMesh, "D")
    D.initialize_cell_with_vector(cell_volumes/U_eqn.diag())
    D_f = calcfield.LinearFaceValue(D)
    grad_p = calcfield.GaussCellGradient(p)
    grad_p_lin_f = calcfield.LinearFaceValue(grad_p)
    grad_p_f = calcfield.GaussFaceGradient(p)
    m_dot.A -= D_f.A * (grad_p_f.dot_Sf()[:,0] - grad_p_lin_f.dot_Sf()[:,0])
    m_dot_old_lin = calcfield.MassFlux(U_old, rho)
    Dt.V /= U_eqn.diag()
    Dt_f = calcfield.LinearFaceValue(Dt)
    m_dot.A += Dt_f.A * (m_dot_old.A - m_dot_old_lin.A)
    
    # assemble pressure-correction equation
    # use pre-calculated coefficient matrix for the laplace operator
    p_corr_eqn += implicit_operators.Laplace(p_corr, 1., "", False)
    p_corr_eqn.A = p_lapl.A
    p_corr_eqn -= explicit_operators.Divergence(None, m_dot, "")
    p_corr_eqn.b[:,0] *= U_eqn.diag() / cell_volumes
    # solve pressure-correction equation
    p_corr_eqn.solve()
    
    # correct pressure
    p.V += p_corr.V * p_under_relaxation
    # correct velocity components
    p_corr.update_boundary_values()
    grad_p_corr = calcfield.GaussCellGradient(p_corr)
    U.V[:,0] -= grad_p_corr.V[:,0] * cell_volumes/U_eqn.diag() * p_under_relaxation
    U.V[:,1] -= grad_p_corr.V[:,1] * cell_volumes/U_eqn.diag() * p_under_relaxation
    U.V[:,2] -= grad_p_corr.V[:,2] * cell_volumes/U_eqn.diag() * p_under_relaxation
        
    # calculate corrected massflux field
    U.update_boundary_values()
    U_old = U
    m_dot = calcfield.MassFlux(U, rho)
    m_dot_old.A = m_dot.A
    
    # monitor remaining divergence
    div = calcfield.Divergence(None, m_dot, "")
    print "corrector divergence: "+str(max(abs(div.V)))
    p_corr_eqn.append_to_monitor(max(abs(div.V)), "divU_corr")
    
    # monitor average pressure
    p_V = numpy.array([p.V[i_]*cell_volumes[i_] for i_ in xrange(len(myMesh.cells))])
    p_ave = p_V.sum() / cell_volumes.sum()
    p_corr_eqn.append_to_monitor(p_ave, "p_average")
        
    myLoop.save_time()
    p_corr_eqn.append_current_residual(myLoop.time, "p")
    U_eqn.append_current_residual(myLoop.time, "U")
    
# end of iteration loop
print "\nfinished test2DchannelFlow in "+str(time.time()-t00)+" s"
