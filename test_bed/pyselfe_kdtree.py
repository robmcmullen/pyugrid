# File:   pyselfe.py

"""
@package docstring
pyselfe : SELFE Model Dataset IO Functions

This module enables the reading of model results generated by SELFE
Works with data format ver.5 SELFE binary files and implements
the extraction of subsets of data from a series of files.

NOTE : Only tested for pure S coordinates. Hybrid S-Z not tested.

Output to other formats not yet implemented.

USAGE EXAMPLE:

sys.path.append('/home/dharhas/scripts/selfe') #path to location of pyselfe.py
import pyselfe
model = pyselfe.Dataset('./data/1_elev.61') #path to first file of series

>>> t, t_iter, eta, dp, mdata = model.read_time_series(param, xy=xy,
...                                                    nfiles=nf,
...                                                    datadir=datadir)

Where param = elev.61, hvel.64 etc.
`read_time_series` is documented in detail below.
"""

from __future__ import (absolute_import, division, print_function)

import os
import sys
import numpy as np
# This custom module is not provided here.  Change it to use `np.fromfile`.
import numpyIO as io
from scipy.spatial import cKDTree as KDTree

__author__ = 'Dharhas Pothina'
__revision__ = "$Revision: 0.1.3 $"
__doc__ = "SELFE Unstructured Grid Ocean Model IO Functions"


class Dataset:
    """
    SELFE Model Binary IO Functions

    Presently enables reading SELFE dataformat version 5.0 binary output files.
    Can read 2D & 3D scalar and vector variables.
    Usage Example:
    model = pyselfe.Dataset('1_hvel.64')
    [t,t_iter,eta,dp,data] = model.read_time_series()
    t = time in seconds
    t_iter = iteration number
    eta = water surface elevation
    dp = bathymetric depth
    data = 2D/3D variables

    @author Dharhas Pothina
    @version 0.2
    """

    def __init__(self, fname, nfiles=1):
        "Initialise by reading header information from file."

        self.fname = fname
        fid = open(fname, 'rb')
        self.read_header(fid)
        self.read_hgrid(fid)
        self.data_start_pos = fid.tell()
        self.compute_step_size()
        self.datadir = os.path.split(fname)[0]
        self.nfiles = nfiles

    def read_header(self, fid):
        """Read header information from SELFE binary output file."""

        # Read misc header info.
        self.data_format = fid.read(48)
        self.version = fid.read(48)
        self.start_time = fid.read(48)
        self.var_type = fid.read(48)
        self.var_dimension = fid.read(48)
        self.nsteps = io.fread(fid, 1, 'i')
        self.dt = io.fread(fid, 1, 'f')
        self.skip = io.fread(fid, 1, 'i')
        self.flag_sv = io.fread(fid, 1, 'i')
        self.flag_dm = io.fread(fid, 1, 'i')

        # @todo check when zDes needs to be read
        # self.zDes = io.fread(fid, 1, 'f').

        # Read vert grid info.
        self.nlevels = io.fread(fid, 1, 'i')
        self.kz = io.fread(fid, 1, 'i')
        self.h0 = io.fread(fid, 1, 'f')
        self.hs = io.fread(fid, 1, 'f')
        self.hc = io.fread(fid, 1, 'f')
        self.theta_b = io.fread(fid, 1, 'f')
        self.theta = io.fread(fid, 1, 'f')
        self.zlevels = io.fread(fid, self.kz, 'f')
        self.slevels = io.fread(fid, self.nlevels-self.kz, 'f')

    def read_hgrid(self, fid):
        """Read horizontal grid info from SELFE binary output file."""

        # Read dimensions.
        self.np = io.fread(fid, 1, 'i')
        self.ne = io.fread(fid, 1, 'i')

        # Read grid and bathymetry.
        pos = fid.tell()
        hgridtmp = io.fread(fid, 4*self.np, 'f')
        self.x, self.y, self.dp, tmp1 = hgridtmp.reshape(self.np, 4).T

        # Read bottom index.
        fid.seek(pos)
        hgridtmp = io.fread(fid, 4*self.np, 'i')
        tmp1, tmp2, tmp3, self.bot_idx = hgridtmp.reshape(self.np, 4).T

        # Read element connectivity list.
        self.elem = io.fread(fid, 4*self.ne, 'i')
        self.elem = self.elem.reshape(self.ne, 4)[:, 1:4]

        # Create kdtree.
        self.kdtree = KDTree(list(zip(self.x, self.y)))

    def compute_step_size(self):
        """
        Compute the data block size to move one timestep within the file.

        """

        # Calculate grid size depending on whether dataset is 3D or 2D.
        if self.flag_dm == 3:
            # @todo check what needs to be done with bIdx (==0?)for dry nodes.
            bIdx = self.bot_idx
            bIdx[bIdx < 1] = 1
            self.grid_size = sum(self.nlevels - bIdx+1)
        elif self.flag_dm == 2:
            self.grid_size = self.np
        # Compute step size.
        self.step_size = 2*4 + self.np*4 + self.grid_size*4*self.flag_sv

    def read_time_series(self, fname, nodes=None, levels=None,
                         xy=np.array([]), nfiles=3, sfile=1, datadir=None):
        """
        Main function to extract a spatial and temporal slice of entire
        3D Time series.

        Returns [t,t_iter,eta,dp,data] where:
        t : time in seconds from simulation start
        t_iter : iteration number from simulation start
        eta : Surface water elevation time series
        dp : Bathymetry (depth of sea bed from MSL)
        data[t,nodes,levels,vars] : extracted data slice
        (i.e. Salinity, Temp, Velocity etc)

        Options:
        nodes : list of nodes to extract (default is all nodes)
        level : list of levels to extract (default is all levels)
        xy : array of x,y coordinates to extract (default is none)
        sfile : serial number of starting file (default is one)
        nfiles : number of files in data sequence (default is one)

        NOTE : node index starts at zero so add one to match up with node
        numbers in SELFE hgrid.gr3 file.

        """

        # Initialize vars.
        t = np.array([])
        t_iter = np.array([])
        eta = []
        data = []

        if nfiles is None:
            nfiles = self.nfiles

        if datadir is None:
            datadir = self.datadir

        # Convert xy points to list of nodes,
        # find parent elements &  calculate interpolation weights.
        if xy.size != 0:
            if xy.shape[1] != 2:
                sys.exit('xy array shape wrong.')
            nodes = np.array([], dtype='int32')
            arco = np.array([])
            for xy00 in xy:
                parent, tmparco, node3 = self.find_parent_element(xy00[0], xy00[1])  # noqa
                nodes = np.append(nodes, node3-1)
                arco = np.append(arco, tmparco)

        # Set default for nodes to be all nodes.
        # Node index starts at zero.
        elif nodes is None:
            nodes = np.arange(self.np)

        # Set default for level to be all levels.
        if levels is None:
            levels = np.arange(self.nlevels)

        # Check whether 2D or 3D variable is being read.
        if self.flag_dm == 2:
            nlevs = 1
            levels = np.array([0])
        else:
            nlevs = self.nlevels

        # Read time series slice.
        for files in np.arange(sfile, sfile + nfiles):
            try:
                fname1 = datadir + '/' + str(files) + '_' + fname
                fid = open(fname1, 'rb')
                fid.seek(self.data_start_pos)
                for i in np.arange(self.nsteps):
                    t = np.append(t, io.fread(fid, 1, 'f'))
                    t_iter = np.append(t_iter, io.fread(fid, 1, 'i'))
                    eta.append(io.fread(fid, self.np, 'f'))
                    tmpdata = io.fread(fid, self.flag_sv*self.grid_size, 'f')
                    tmpdata = tmpdata.reshape(self.np, nlevs, self.flag_sv)
                # Only keep requested slice of tmpdata.
                # i.e. tmpdata[nodes, levels, var]
                    tmpdata = tmpdata[nodes, :, :]
                    tmpdata = tmpdata[:, levels, :]
                    data.append(tmpdata)
            except:
                continue
        # import pdb; pdb.set_trace()
        eta = np.column_stack(eta[:]).T
        eta = eta[:, nodes]
        data = np.array(data)
        dp = self.dp[nodes]

        # Convert nodal values back to xy point values if needed.
        if xy.size != 0:
            # Not sure about this. Need to look at it on more detail put in to
            # remove shape error.
            # try:
            tmpdata = np.zeros((data.shape[0], data.shape[1]//3, data.shape[2], data.shape[3]))/0.  # noqa
            # except:
            #     tmpdata = np.zeros((data.shape[0], data.shape[1]//3, data.shape[2]))/0.  # noqa
            tmpeta = np.zeros((eta.shape[0], eta.shape[1]//3)) / 0.
            tmpdp = np.zeros(dp.shape[0]//3) / 0.
            for i in range(xy.shape[0]):
                n1 = i*3
                n2 = n1+1
                n3 = n2+1
                tmpdata[:, i, :, :] = (data[:, n1, :, :] * arco[n1] +
                                       data[:, n2, :, :] * arco[n2] +
                                       data[:, n3, :, :] * arco[n3])
                tmpeta[:, i] = (eta[:, n1] * arco[n1] +
                                eta[:, n2] * arco[n2] +
                                eta[:, n3] * arco[n3])
                tmpdp[i] = (dp[n1] * arco[n1] +
                            dp[n2] * arco[n2] +
                            dp[n3] * arco[n3])
            data = tmpdata
            eta = tmpeta
            dp = tmpdp

        return t, t_iter, eta, dp, data

    def find_parent_element(self, x00, y00):
        """
        Find Parent Element of a given (x,y) point and calculate
        interpolation weights.

        Uses brute force search through all elements.
        Calculates whether point is internal/external to element by comparing
        summed area of sub triangles with area of triangle element.
        @todo implement binary tree search for efficiency

        Returns:
        parent, arco, node3 : parent element number, interp wieghts and element
        node numbers.

        """

        def signa(x1, x2, x3, y1, y2, y3):
            "Return signed area of triangle."
            return(((x1-x3)*(y2-y3)-(x2-x3)*(y1-y3))/2)

        parent = -1
        nm = self.elem.view()
        out = np.zeros(3)/0.
        x = self.x.view()
        y = self.y.view()
        for i in np.arange(self.ne):
            aa = 0
            ar = 0  # Area.
            for j in np.arange(3):
                j1 = j+1
                j2 = j+2
                if (j1 > 2):
                    j1 = j1-3
                if (j2 > 2):
                    j2 = j2-3
                n0 = nm[i, j]-1  # Zero based index rather than 1 based index.
                n1 = nm[i, j1]-1
                n2 = nm[i, j2]-1
                # Temporary storage.
                out[j] = signa(x[n1], x[n2], x00, y[n1], y[n2], y00)
                aa = aa+abs(out[j])
                if (j == 0):
                    ar = signa(x[n1], x[n2], x[n0], y[n1], y[n2], y[n0])

            if (ar <= 0):
                sys.exit('Negative area:' + str(ar))

            ae = abs(aa-ar)/ar
            if (ae <= 1.e-5):
                parent = i
                node3 = nm[i, 0:3]
                arco = out[0:3] / ar
                arco[1] = max(0., min(1., arco[1]))
                arco[2] = max(0., min(1., arco[2]))
                if (arco[0] + arco[1] > 1):
                    arco[2] = 0
                    arco[1] = 1-arco[0]
                else:
                    arco[2] = 1-arco[0]-arco[1]
                break
        if (parent == -1):
            sys.exit('Cannot find a parent:' + str(x00) + ',' + str(y00))
        else:
            print('Parent Element :', parent+1, ' ,Nodes: ', node3)
            return parent, arco, node3

    def compute_relative_rec(self, node, level):
        """
        Computes offset for extracting particular node/level.
        NOTE THIS FUNCTION NOT COMPLETE/TESTED.

        """
        count = 0
        step_size = np.zeros(self.np, self.nlevels, self.flag_sv) / 0.
        for i in range(self.np):
            for k in range(max(1, self.bot_idx[i]), self.nlevels):
                for m in range(self.flag_sv):
                    count = count+1
                    step_size[i, k, m] = count

    def read_time_series_xy(self, variable, x, y,
                            sigma_level='middle', return_eta=False):
        """
        Finds nearest 3 nodes to x,y and returns the average value.

        """
        xy = np.hstack((x, y))
        dist, nodes = self.kdtree.query(xy, k=3)
        data = []

        if sigma_level == 'average':
            t, t_iter, eta, dp, data = self.read_time_series(variable, nodes=nodes)  # noqa
            eta = eta.mean(axis=1)
            data = data[:, :, :, 0].mean(axis=2).mean(axis=1)
            # Take average of all levels and then 3 nodes for now.
            # Implement idw or area weighted a average later.
            data = data.mean(axis=1).mean(axis=1)
            if return_eta:
                return np.column_stack((t, data)), np.column_stack((t, eta))
            else:
                return np.column_stack((t, data))

        elif sigma_level == 'top':
            sigma_level = 0
        elif sigma_level == 'bottom':
            sigma_level = self.nlevels - 1
        elif sigma_level == 'middle':
            sigma_level = self.nlevels // 2

        t, t_iter, eta, dp, data = self.read_time_series(variable,
                                                         nodes=nodes,
                                                         levels=sigma_level)
        eta = eta.mean(axis=1)
        data = data[:, :, 0, :].mean(axis=1)
        # data.mean(axis=1).shape[:, 0, :]
        # Take average of all levels and then 3 nodes for now.
        # Implement idw or area weighted average later/
        # data = data.mean(axis=1)
        # import pdb; pdb.set_trace()
        if return_eta:
            return np.column_stack((t, data)), np.column_stack((t, eta))
        else:
            return np.column_stack((t, data))
