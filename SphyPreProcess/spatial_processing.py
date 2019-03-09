# The SPHY model Pre-Processor interface plugin for QGIS:
# A QGIS plugin that allows the user to create SPHY model input data based on a database. 
#
# Copyright (C) 2015  Wilco Terink
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Email: terinkw@gmail.com

#-Authorship information-###################################################################
__author__ = "Wilco Terink"
__copyright__ = "Wilco Terink"
__license__ = "GPL"
__version__ = "1.0.0"
__email__ = "terinkw@gmail.com"
__date__ ='1 January 2017'
############################################################################################

import os

#-Class with gdal processing commands
class SpatialProcessing():
    def __init__(self, infile, outfile, s_srs, t_srs, resolution, resampling='bilinear', rtype='Float32', extra=None, projwin=None):
        self.input = infile
        self.output = outfile
        self.s_srs = s_srs
        self.t_srs = t_srs
        self.t_res = str(resolution)
        self.resampling = resampling
        self.rtype = rtype
        self.extra = extra
        self.projwin = projwin
        
    #-Reproject, resample, and clip to extent
    def reproject(self):
        command = 'gdalwarp -s_srs ' + self.s_srs + ' -t_srs ' + self.t_srs + ' -r ' + self.resampling\
                        + ' -tr ' + self.t_res + ' ' + self.t_res +' -ot ' + self.rtype + ' ' + self.extra\
                        + ' ' + self.input + ' ' + self.output 
        return command
    
    #-Convert raster format
    def rasterTranslate(self):
        command = 'gdal_translate ' + self.extra + ' ' + self.input + ' ' + self.output
        return command
    
    #-Rasterize Vector
    def rasterize(self):
        d = os.path.dirname(self.input)
        fi = os.path.relpath(self.input, d)
        layer = fi.split('.shp')[0]
        command = 'gdal_rasterize ' + self.extra + ' -l ' + layer + ' -tr ' + self.t_res + ' ' + \
            self.t_res + ' ' + self.input + ' ' + self.output
        return command
    
