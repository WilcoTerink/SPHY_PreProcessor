# -*- coding: utf-8 -*-
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

"""
/***************************************************************************
 SphyPreProcessDialog
                                 A QGIS plugin
 A tool to convert raw data into SPHY model input data
                             -------------------
        begin                : 2015-06-23
        git sha              : $Format:%H$
        copyright            : (C) 2015 by Wilco Terink
        email                : terinkw@gmail.com
 ***************************************************************************/

"""

import os, subprocess, ConfigParser, sqlite3, datetime, math, glob, time, processing

from PyQt4 import QtGui, QtCore #uic
from qgis.core import *
from qgis.utils import iface, plugins
from qgis.gui import QgsMessageBar, QgsMapToolEmitPoint, QgsRubberBand

from SPHY_preprocess_dialog_base import Ui_SphyPreProcessDialog
#from PyQt4.Qt import QMessageBox
#from Canvas import Rectangle
#from PyQt4.Qt import QFileInfo
#from PyQt4.Qt import QFileInfo, QMessageBox

#-Import spatial processing class with gdal commands
from spatial_processing import SpatialProcessing
#-Import worker class for running subprocesses in a thread
from worker import SubProcessWorker
#-Import forcing processing 
from forcing import processForcing
#from win32con import WAIT_IO_COMPLETION
#import shutil

#-Class that allows to drag a rectangle on the map canvas
class RectangleMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas):
        self.canvas = canvas
        QgsMapToolEmitPoint.__init__(self, self.canvas)
        self.rubberBand = QgsRubberBand(self.canvas, QGis.Polygon)
        clr = QtGui.QColor('red')
        clr.setAlpha(50)
        self.rubberBand.setFillColor(clr)
        self.rubberBand.setWidth(1)
        self.reset()
    
    def reset(self):
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBand.reset(QGis.Polygon)
    
    def canvasPressEvent(self, e):
        self.startPoint = self.toMapCoordinates(e.pos())
        self.endPoint = self.startPoint
        self.isEmittingPoint = True
        self.showRect(self.startPoint, self.endPoint)
    
    def canvasReleaseEvent(self, e):
        self.isEmittingPoint = False
        r = self.rectangle()
        if r is not None:
            self.rubberBand.reset()
            self.finished.emit(r)
    
    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return
        self.endPoint = self.toMapCoordinates(e.pos())
        self.showRect(self.startPoint, self.endPoint)
    
    def showRect(self, startPoint, endPoint):
        self.rubberBand.reset(QGis.Polygon)
        if startPoint.x() == endPoint.x() or startPoint.y() == endPoint.y():
            return
        point1 = QgsPoint(startPoint.x(), startPoint.y())
        point2 = QgsPoint(startPoint.x(), endPoint.y())
        point3 = QgsPoint(endPoint.x(), endPoint.y())
        point4 = QgsPoint(endPoint.x(), startPoint.y())

        self.rubberBand.addPoint(point1, False)
        self.rubberBand.addPoint(point2, False)
        self.rubberBand.addPoint(point3, False)
        self.rubberBand.addPoint(point4, True)    # true to update canvas
        self.rubberBand.show()
    
    def rectangle(self):
        if self.startPoint is None or self.endPoint is None:
            return None
        elif self.startPoint.x() == self.endPoint.x() or self.startPoint.y() == self.endPoint.y():
            return None
        return QgsRectangle(self.startPoint, self.endPoint)
    
    def deactivate(self):
        super(RectangleMapTool, self).deactivate()
        self.emit(QtCore.SIGNAL("deactivated()"))
    
    finished = QtCore.pyqtSignal(object)

#-Preprocessor
class SphyPreProcessDialog(QtGui.QDialog, Ui_SphyPreProcessDialog):
    def __init__(self, parent=None):
        """Constructor."""
        super(SphyPreProcessDialog, self).__init__(parent)
        # Set up the user interface from Designer.
        # After setupUI you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)
        #-Define the path where the plugins are installed
        self.pluginPath = os.path.dirname(__file__) + '/'
        
        #-Trigger the PYthon console in order to let the print statements work in the thread subprocessing part
        iface.actionShowPythonDialog().trigger()
        iface.actionShowPythonDialog().trigger()
        
        """
        If QGIS is loaded, check if there is a recent SPHY preprocessor config file in the registry
        if not, then create a reference to the SPHY preprocessor config template and initialize the plugin
        with that template file.
        """
        # Check if an existing config file is present from the most recent project
        self.currentConfig = ConfigParser.ConfigParser(allow_no_value = True)
        self.settings = QtCore.QSettings()
        self.currentConfigFileName = self.settings.value("sphyPreProcessPlugin/currentConfig")
        try:
            self.currentConfig.read(self.currentConfigFileName)
            self.projectDir = os.path.dirname(self.currentConfigFileName) + '/'
            self.currentProject = True
            self.Tab.setEnabled(1)
            #self.Tab.setEnabled(self, 3, False)
            self.initGuiConfigMap()
            self.saveAsButton.setDisabled(0)
        except:
            self.currentProject = False
            self.Tab.setEnabled(0)
            self.projectDir = './'
            self.databasePath = './'
            self.resultsPath = './'
            self.pcrBinPath = './'
            self.saveAsButton.setDisabled(1)
            
        #self.saveButton.setDisabled(1)
        
        #-Detect and set coordinate systems
        self.mapCrs = iface.mapCanvas().mapRenderer().destinationCrs()
        try:
            crs = self.lookupUTM(self.currentConfig.getint('GENERAL', 'utmZoneNr'), self.currentConfig.get('GENERAL', 'utmZoneStr'))
            self.userCRS = QgsCoordinateReferenceSystem("EPSG:" + str(crs))
        except:
            self.userCRS = self.mapCrs
        
        """ Menu buttons (New Project, Open Project, Save, Save As) """    
        self.newButton.clicked.connect(self.createNewProject)
        self.openButton.clicked.connect(self.openProject)
        #self.saveButton.clicked.connect(self.saveProject)
        self.saveAsButton.clicked.connect(self.saveAsProject)
        
        """ General settings Tab """
        #-Set the folders
        self.databaseFolderButton.clicked.connect(self.updatePath)
        self.resultsFolderButton.clicked.connect(self.updatePath)
        self.pcrasterBinFolderButton.clicked.connect(self.updatePath)
        #-User specified coordinate system
        self.utmSpinBox.valueChanged.connect(self.changeCRS)
        self.utmNRadioButton.toggled.connect(self.changeCRS)
        self.showUTMMapButton.clicked.connect(self.showUTM)
        #-Date settings
        self.startDateEdit.dateChanged.connect(self.updateDate)
        self.endDateEdit.dateChanged.connect(self.updateDate)
        
        """ Area selection Tab """
        self.showBackgroundMapcheckBox.stateChanged.connect(self.showBackground)
        self.selectAreaButton.clicked.connect(self.selectArea)
        self.calculateAreaPropsButton.clicked.connect(self.recreateArea)
        self.createModelCloneButton.clicked.connect(self.createClone)
        
        """ Modules Tab """
        self.glacierModCheckBox.stateChanged.connect(self.updateModules)
        self.routingModCheckBox.stateChanged.connect(self.updateModules)
        self.snowModCheckBox.stateChanged.connect(self.updateModules)
        self.groundwaterModCheckBox.stateChanged.connect(self.updateModules)
        self.createInitialMapsToolButton.clicked.connect(self.createInitMaps)
        
        """ Basin delineation Tab """
        self.selectOutletsButton.clicked.connect(self.updateDelineation)
        self.clipMaskCheckBox.stateChanged.connect(self.updateDelineation)
        self.createSubBasinCheckBox.stateChanged.connect(self.updateDelineation)
        self.delineateButton.clicked.connect(self.delineate)
        
        """ Stations Tab """
        self.selectStationsButton.clicked.connect(self.updateStations)
        self.stationsButton.clicked.connect(self.createStations)
        
        
        """ Meteorological forcing Tab """
        self.precFlagCheckBox.stateChanged.connect(self.updateForcing)
        self.tempFlagCheckBox.stateChanged.connect(self.updateForcing)
        self.precDBRadioButton.toggled.connect(self.updateForcing)
        self.tempDBRadioButton.toggled.connect(self.updateForcing)
        self.precLocToolButton.clicked.connect(self.updateForcing)
        self.tempLocToolButton.clicked.connect(self.updateForcing)
        self.precDataToolButton.clicked.connect(self.updateForcing)
        self.tempDataToolButton.clicked.connect(self.updateForcing)
        self.forcingToolButton.clicked.connect(self.createForcing)
        
        #-clear the process log text widget
        self.processLog1TextEdit.clear()
        self.processLog2TextEdit.clear()
        self.processLog3TextEdit.clear()
        self.processLog4TextEdit.clear()
        
    #-Initialize the GUI
    def initGuiConfigMap(self):
        #####-Dictionary for General settings Tab and Basin delineation Tab
        self.configDict = {'databaseLineEdit':('GENERAL', 'Database_dir'), 'resultsLineEdit':('GENERAL', 'Results_dir'), 'pcrasterBinFolderLineEdit': ('GENERAL', 'Pcraster_dir'),\
                           'utmSpinBox': ('GENERAL', 'utmZoneNr'),'startDateEdit': ('GENERAL', ('startyear', 'startmonth', 'startday')), 'endDateEdit': ('GENERAL', \
                           ('endyear', 'endmonth', 'endday')), 'outletsLineEdit' : ('DELINEATION', 'outlets_shp'), 'clipMaskCheckBox' : ('DELINEATION', 'clip'),\
                           'createSubBasinCheckBox' : ('DELINEATION', 'subbasins'), 'stationsLineEdit' : ('STATIONS', 'stations_shp')}
        self.setGui()
        #####-Dictionary for UTM coordinate system
        self.configRadioDict = {'utmNRadioButton': ('GENERAL', 'utmZoneStr'), 'utmSRadioButton': ('GENERAL', 'utmZoneStr')}
        self.setRadioGui()
        #####-Dictionary for Area selection Tab        
        self.configAreaDict = {'selectedAreaMapLineEdit': ('AREA', 'clone_shp'), 'spatialResolutionSpinBox': ('AREA', 'resolution'), 'numberCellsLineEdit': ('AREA', 'cells'),\
                               'areaSizeLineEdit': ('AREA', 'area'),'xminLineEdit': ('AREA', 'xmin'),'xmaxLineEdit': ('AREA', 'xmax'),'ymaxLineEdit': ('AREA', 'ymax'),\
                               'yminLineEdit': ('AREA', 'ymin'), 'columnsLineEdit': ('AREA', 'cols'), 'rowsLineEdit': ('AREA', 'rows'), 'cloneLineEdit': ('AREA', 'clone_grid')}
        self.setAreaDict()
        #####-Dictionary for Modules Tab
        self.configModulesDict = {'glacierModCheckBox': ('MODULES', 'glacier'), 'snowModCheckBox': ('MODULES', 'snow'), 'groundwaterModCheckBox': ('MODULES', 'groundwater'),\
                                  'routingModCheckBox': ('MODULES', 'routing')}
        #-general maps are always created in the "Create initial maps" Tab 
        self.generalMaps = {'DEM': 'dem.map', 'Slope': 'slope.map', 'Root_field': 'root_field.map', 'Root_sat': 'root_sat.map',\
                            'Root_dry': 'root_dry.map', 'Root_wilt': 'root_wilt.map', 'Root_Ksat': 'root_ksat.map', 'Sub_field': 'sub_field.map', 'Sub_sat': 'sub_sat.map',\
                            'Sub_Ksat': 'sub_ksat.map', 'LandUse': 'landuse.map', 'Latitudes': 'latitude.map'}
        #-glacier and routing maps are only created if these modules are turned on. Snow and groundwater modules don't require the creation of maps, but are implemented for possible
        # future developments. The Gui doesn't do anything with these two modules yet.
        self.glacierMaps = {'GlacFrac': 'glacfrac.map', 'GlacFracCI': 'glac_cleanice.map', 'GlacFracDB': 'glac_debris.map'}
        #self.routingMaps = {'LDD': 'ldd.map', 'Outlets': 'outlet.map', 'Rivers': 'river.map', 'AccuFlux': 'accuflux.map', 'Sub-basins': 'subbasins.map'}
        self.routingMaps = {'LDD': 'ldd.map', 'Outlets': 'outlets.map', 'Rivers': 'river.map', 'AccuFlux': 'accuflux.map', 'Sub-basins': 'subbasins.map'}
        self.setModulesDict()
        #-Dictionary for the Meteorological forcing Tab
        self.forcingDict = {'FlagCheckBox': 'FLAG', 'DBRadioButton': 'DB', 'LocFileLineEdit': 'LocFile', 'DataFileLineEdit': 'DataFile'}        
        self.setForcingDict()
    
    #-Set the GUI with the correct values (obtained from config file)
    def setGui(self):
        for key in self.configDict:
            widget = eval('self.' + key)
            i = self.configDict[key]
            module = i[0]
            pars = i[1]
            if module == 'GENERAL' and (pars[0] == 'startyear' or pars[0] == 'endyear'): 
                #self.setGui(widget, module, pars[0], pars[1], pars[2])
                widget.setDate(QtCore.QDate(self.currentConfig.getint(module, pars[0]),self.currentConfig.getint(module, pars[1]),self.currentConfig.getint(module, pars[2])))
                #self.updateDate()
            else:
                if isinstance(widget, QtGui.QLineEdit):
                    widget.setText(self.currentConfig.get(module, pars))
                elif isinstance(widget, QtGui.QCheckBox):
                    if self.currentConfig.getint(module, pars) == 1:
                        widget.setChecked(1)
                elif isinstance(widget, QtGui.QDoubleSpinBox):
                    widget.setValue(self.currentConfig.getfloat(module, pars))
                elif isinstance(widget, QtGui.QSpinBox):
                    widget.setValue(self.currentConfig.getint(module, pars))
                # define the variables self.databasePath and self.resultsPath and pcraster bin path. Set the database metadata config file as well.
                if widget == self.databaseLineEdit:
                    self.databasePath = self.currentConfig.get(module, pars)
                    if os.path.isfile(os.path.join(self.databasePath, 'metadata.cfg')):
                        self.databaseConfig = ConfigParser.ConfigParser(allow_no_value = True)
                        self.databaseConfig.read(os.path.join(self.databasePath, 'metadata.cfg'))
                    else:
                        self.databaseConfig = False
                elif widget == self.resultsLineEdit:
                    self.resultsPath = self.currentConfig.get(module, pars)
                elif widget == self.pcrasterBinFolderLineEdit:
                    self.pcrBinPath = self.currentConfig.get(module, pars)
                #-Check if a outlet(s) file has been defined by the user
                elif widget == self.outletsLineEdit:
                    if os.path.exists(self.currentConfig.get(module, pars)):
                        self.outletsShp = self.currentConfig.get(module, pars)
                        widget.setText(self.outletsShp)
                    else:
                        self.outletsShp = False
                #-Check if a station(s) file has been defined by the user
                elif widget == self.stationsLineEdit:
                    if os.path.exists(self.currentConfig.get(module, pars)):
                        self.stationsShp = self.currentConfig.get(module, pars)
                        widget.setText(self.stationsShp)
                    else:
                        self.stationsShp = False
        #-initialize the enddate and startdate for processing forcing data
        self.enddate = datetime.date(QtCore.QDate.year(self.endDateEdit.date()),QtCore.QDate.month(self.endDateEdit.date()),QtCore.QDate.day(self.endDateEdit.date()))
        self.startdate = datetime.date(QtCore.QDate.year(self.startDateEdit.date()),QtCore.QDate.month(self.startDateEdit.date()),QtCore.QDate.day(self.startDateEdit.date()))
                
    #-Set the GUI radio buttons (obtained from config file)
    def setRadioGui(self):
        for key in self.configRadioDict:
            i = self.configRadioDict[key]
            module = i[0]
            pars = i[1]
            utmStr = self.currentConfig.get(module, pars)
            if key == 'utmNRadioButton' and utmStr == 'N':
                self.utmNRadioButton.setChecked(1)
            elif key == 'utmSRadioButton' and utmStr == 'S':
                self.utmSRadioButton.setChecked(1)
                
    #-Set the GUI for the Area selection (obtained from config file)
    def setAreaDict(self):
        for key in self.configAreaDict:
            widget = eval('self.' + key)
            i = self.configAreaDict[key]
            module = i[0]
            pars = i[1]
            if isinstance(widget, QtGui.QLineEdit):
                widget.setText(self.currentConfig.get(module, pars))
            elif isinstance(widget, QtGui.QSpinBox):
                widget.setValue(self.currentConfig.getint(module, pars))
            #-check if the user already has a clone shapefile of the area
            if widget == self.selectedAreaMapLineEdit:
                self.selectedAreaShp = self.currentConfig.get(module, pars)
                if os.path.exists(self.selectedAreaShp):
                    self.areaPropertiesGroupBox.setEnabled(1)
                else:
                    self.selectedAreaShp = False
                    self.areaPropertiesGroupBox.setEnabled(0)
            #-set the desired spatial resolution and extents
            elif widget == self.spatialResolutionSpinBox:
                self.spatialRes = self.currentConfig.getint(module, pars)
            elif widget == self.xminLineEdit:
                self.xMin = self.currentConfig.getint(module, pars)
            elif widget == self.xmaxLineEdit:
                self.xMax = self.currentConfig.getint(module, pars)
            elif widget == self.yminLineEdit:
                self.yMin = self.currentConfig.getint(module, pars)
            elif widget == self.ymaxLineEdit:
                self.yMax = self.currentConfig.getint(module, pars)
            elif widget == self.rowsLineEdit:
                self.rows = self.currentConfig.getint(module, pars)
            elif widget == self.columnsLineEdit:
                self.cols = self.currentConfig.getint(module, pars)
            #-set the clone
            elif widget == self.cloneLineEdit:
                self.clone = self.currentConfig.get(module, pars)
    
    #-Set the GUI for the Modules
    def setModulesDict(self):
        #-Clear the widget list
        self.modulesListWidget.clear()
        #-First set the modules to False (only routing and glacier are implemented because they require creation of maps)
        self.routing = False
        self.Tab.setTabEnabled(3, False)
        self.glacier = False
        #-Add the general maps to the list widget
        for k in self.generalMaps:
            self.modulesListWidget.addItem(k)
        #-check with modules to use
        for key in self.configModulesDict:
            widget = eval('self.' + key)
            i = self.configModulesDict[key]
            module = i[0]
            pars = i[1]
            widget.setChecked(self.currentConfig.getint(module, pars))
            if widget == self.routingModCheckBox and self.routingModCheckBox.checkState():
                self.routing = True
                self.Tab.setTabEnabled(3, True)
                for k in self.routingMaps:
                    self.modulesListWidget.addItem(k)
            elif widget == self.glacierModCheckBox and self.glacierModCheckBox.checkState():
                self.glacier = True
                for k in self.glacierMaps:
                    self.modulesListWidget.addItem(k)
        #-Sort the list
        self.modulesListWidget.sortItems()
        
    #-Set the GUI for the Meteorological forcing
    def setForcingDict(self):
        forcings = ['prec','temp']
        for f in forcings:
            for key in self.forcingDict:
                widget = eval('self.' + f +  key)
                pars = f + self.forcingDict[key]
                if isinstance(widget, QtGui.QCheckBox) or isinstance(widget, QtGui.QRadioButton):
                    checked = self.currentConfig.getint('FORCING', pars)
                    widget.setChecked(checked)
                    if widget == eval('self.' + f + 'FlagCheckBox'):
                        eval('self.' + f + 'GroupBox.setEnabled(checked)')
                        if checked == 1:
                            setattr(self, f + 'FLAG', True)
                        else:
                            setattr(self, f + 'FLAG', False)
                    elif widget == eval('self.' + f + 'DBRadioButton'):
                        if checked == 1:
                            setattr(self, f + 'DB', True)
                            setattr(self, f + 'CSV', False)
                            eval('self.' + f + 'FilesGroupBox.setEnabled(0)')
                        else:
                            setattr(self, f + 'DB', False)
                            setattr(self, f + 'CSV', True)
                            eval('self.' + f + 'FilesGroupBox.setEnabled(1)')
                            if os.path.isfile(self.currentConfig.get('FORCING', f + 'LocFile')) and os.path.isfile(self.currentConfig.get('FORCING', f + 'DataFile')):
                                setattr(self, f + 'LocFile', self.currentConfig.get('FORCING', f + 'LocFile'))
                                setattr(self, f + 'DataFile', self.currentConfig.get('FORCING', f + 'DataFile'))
                            else:
                                setattr(self, f + 'LocFile', False)
                                setattr(self, f + 'DataFile', False)
                            eval('self.' + f + 'CSVRadioButton.setChecked(1)')
                else:
                    widget.setText(self.currentConfig.get('FORCING', pars))
                
    #-Function that updates the paths in the GUI, and updates the config file
    def updatePath(self):
        sender =  self.sender().objectName()
        if sender == 'databaseFolderButton':
            tempname = QtGui.QFileDialog.getExistingDirectory(self, 'Select the database folder for your area of interest', self.projectDir, QtGui.QFileDialog.ShowDirsOnly)
            if tempname and os.path.isfile(os.path.join(tempname, 'metadata.cfg')):
                self.databaseLineEdit.setText(tempname.replace('\\', '/') + '/')
                self.updateConfig('GENERAL', 'Database_dir', tempname.replace('\\', '/') + '/')
            else:
                iface.messageBar().pushMessage('Error:', 'No database found in the specified folder.\nSelect a different folder.' , QgsMessageBar.CRITICAL, 10)
        elif sender == 'resultsFolderButton':
            tempname = QtGui.QFileDialog.getExistingDirectory(self, 'Select the folder where processed model input should be written', self.projectDir, QtGui.QFileDialog.ShowDirsOnly)
            if tempname:
                self.resultsLineEdit.setText(tempname.replace('\\', '/') + '/')
                self.updateConfig('GENERAL', 'Results_dir', tempname.replace('\\', '/') + '/')
        elif sender == 'pcrasterBinFolderButton':
            tempname = QtGui.QFileDialog.getExistingDirectory(self, 'Select the PCRaster bin folder', self.projectDir, QtGui.QFileDialog.ShowDirsOnly)
            if os.path.isfile(os.path.join(tempname, 'pcrcalc.exe')) == False:
                QtGui.QMessageBox.warning(self, 'PCRaster bin path error', 'PCRaster is not found in the specified folder. \nSelect a different folder.')
            else:
                self.pcrasterBinFolderLineEdit.setText(tempname.replace('\\', '/') + '/')
                self.updateConfig('GENERAL', 'Pcraster_dir', tempname.replace('\\', '/') + '/')
        self.saveProject()

    #-Update the config file        
    def updateConfig(self, module, par, value):
        self.currentConfig.set(module, par, str(value))
        self.updateSaveButtons(1)
        
    #-Update config with area properties
    def updateAreaConfig(self):
        self.updateConfig('AREA', 'clone_shp', self.selectedAreaShp)
        self.updateConfig('AREA', 'area', '%s' %int(self.area))
        self.updateConfig('AREA', 'cells', '%s' %int(self.cells))
        self.updateConfig('AREA', 'xmin', '%s' %int(self.xMin))
        self.updateConfig('AREA', 'xmax', '%s' %int(self.xMax))
        self.updateConfig('AREA', 'ymin', '%s' %int(self.yMin))
        self.updateConfig('AREA', 'ymax', '%s' %int(self.yMax))
        self.updateConfig('AREA', 'cols', '%s' %int(self.cols))
        self.updateConfig('AREA', 'rows', '%s' %int(self.rows))
        self.updateConfig('AREA', 'resolution', self.spatialRes)
                    
       
    #-Function that shows a UTM jpg image to help the user identify which UTM zone to use    
    def showUTM(self):
        subprocess.call(self.pluginPath + 'utm_zones.jpg', shell=True)
    
    #-Add or remove background layer (from NaturalEarth)    
    def showBackground(self, state):
        if state==QtCore.Qt.Unchecked:
            #-try to remove the background maps if they already exist in the canvas
            try:
                canvas = iface.mapCanvas()
                allLayers = canvas.layers()
                for i in allLayers:
                    if i.name() == 'shaded relief' or i.name() == 'countries':
                        QgsMapLayerRegistry.instance().removeMapLayer(i.id())
            except:
                pass
        #-Show the background layers
        else:
            raster = iface.addRasterLayer(self.pluginPath + 'NaturalEarthData/HYP_50M_SR_W/HYP_50M_SR_W.tif', 'shaded relief')
            raster.setCrs(QgsCoordinateReferenceSystem(4326, QgsCoordinateReferenceSystem.EpsgCrsId))
            countries = iface.addVectorLayer(self.pluginPath + 'NaturalEarthData/ne_10m_admin_0_countries.shp', 'countries', 'ogr')
            countries.setCrs(QgsCoordinateReferenceSystem(4326, QgsCoordinateReferenceSystem.EpsgCrsId))
            fill_style = QgsSimpleFillSymbolLayerV2()
            fill_style.setBrushStyle(QtCore.Qt.NoBrush)
            countries.rendererV2().symbols()[0].changeSymbolLayer(0, fill_style)
            iface.mapCanvas().refresh()
            iface.legendInterface().refreshLayerSymbology(countries)

    #-Function to select a rectangle on the map for the area of interest.
    def selectArea(self):
        self.hide()
        self.deleteSelectedArea()
        iface.messageBar().pushMessage('Action:', 'Drag a rectangle for your area of interest.' , QgsMessageBar.INFO, 0)
        canvas = iface.mapCanvas()        
        self.selectRectangle = RectangleMapTool(canvas)
        canvas.setMapTool(self.selectRectangle)
        self.selectRectangle.finished.connect(self.areaSelectionFinished)
    
    #-Function that is called as soon as a rectangle is drawn from the selectArea function
    def areaSelectionFinished(self, rectangle):
        canvas = iface.mapCanvas()
        canvas.unsetMapTool(self.selectRectangle)
        iface.messageBar().clearWidgets()
        self.show()
        #-project rectangle to user defined utm to get the correct coordinates
        self.mapCrs = iface.mapCanvas().mapRenderer().destinationCrs()
        rectangle = self.coordinateTransform(self.mapCrs, self.userCRS, rectangle)
        #-if rectangle is valid, then create clone polygon shapefile
        if rectangle:
            #-calculate correct extent properties
            rectangle = self.calculateExtent(rectangle)
            #-Transform the coordinates of the correct rectangle extent to the mapCrs for drawing
            rectangleMapCrs = self.coordinateTransform(self.userCRS, self.mapCrs, rectangle)
            #-Create polygone shapefile of the selected area
            self.createPolygonArea(rectangleMapCrs)
            #-Update the config for the area settings 
            self.updateAreaConfig()
        else:
            iface.messageBar().pushMessage('Error:', 'UTM zone is not valid for selected area. Select a different area.', QgsMessageBar.CRITICAL, 10)
            #-deactivate the lineedit and groupbox for area properties
            self.updateConfig('AREA', 'clone_shp', '')
        self.saveProject()
    
    #-Create shapefile of selected area
    def createPolygonArea(self, rectangle):
        #-Create a memory vector layer
        vl = QgsVectorLayer('Polygon', 'Selected area', 'memory')
        vl.startEditing()
        fet = QgsFeature()
        fet.setGeometry(QgsGeometry.fromRect(rectangle))
        vl.addFeature(fet)
        iface.messageBar().popWidget() #   -> uncomment to remove epsg crs warning
        #-Write the polygone to a vector shapefile
        QgsVectorFileWriter.writeAsVectorFormat(vl, self.selectedAreaShp, 'Selected area', self.mapCrs, 'ESRI Shapefile')
        #-Add the created vector shapefile to the canvas
        self.addSelectedArea()

    #-Delete the selected area shapefile if it already exists (from canvas and from disk)       
    def deleteSelectedArea(self):
        if self.selectedAreaShp:
            for l in iface.mapCanvas().layers():
                #-Remove the layer if it already exists
                if l.name() == 'Selected area':
                    QgsMapLayerRegistry.instance().removeMapLayer(l.id())
                    #QgsVectorFileWriter.deleteShapeFile(l.source())
        else:
            #-Define the shapefile
            self.selectedAreaShp = self.resultsPath + 'area.shp'
            
    #-Add selected polygon area to canvas
    def addSelectedArea(self):
        fill_style = QgsSimpleFillSymbolLayerV2()
        clr = QtGui.QColor('red')
        clr.setAlpha(50)
        fill_style.setColor(clr)
        wb = QgsVectorLayer(self.selectedAreaShp, 'Selected area', 'ogr')
        #wb.setCrs(self.mapCrs)
        wb.rendererV2().symbols()[0].changeSymbolLayer(0, fill_style)
        QgsMapLayerRegistry.instance().addMapLayer(wb, False)
        root = QgsProject.instance().layerTreeRoot()
        root.insertLayer(0, wb)
        iface.mapCanvas().refresh()
        iface.legendInterface().refreshLayerSymbology(wb)
        
    #-Function to calculate selected area properties
    def calculateExtent(self, rectangle):
        self.xMin = math.floor(rectangle.xMinimum())
        self.xMax = math.ceil(rectangle.xMaximum())
        self.yMin = math.floor(rectangle.yMinimum())
        self.yMax = math.ceil(rectangle.yMaximum())
        self.cols = math.ceil((self.xMax - self.xMin) / self.spatialRes)
        self.rows = math.ceil((self.yMax - self.yMin) / self.spatialRes)
        self.xMax = self.xMin + self.cols * self.spatialRes
        self.yMax = self.yMin + self.rows * self.spatialRes
        self.cells = self.cols * self.rows
        #-give warning if cells are too much
        if self.cells > 500000 and self.cells <= 1000000:
            iface.messageBar().pushMessage('Warning:', 'Your model has > 500,000 cells. This means that model run-time will likely be > 1 hour for a 10-year simulation period. Choose a larger spatial resolution to reduce model run-time.', QgsMessageBar.WARNING, 10)
        if self.cells > 1000000 and self.cells <=2000000:
            iface.messageBar().pushMessage('Warning:', 'Your model has > 1,000,000 cells. This means that model run-time will likely be > 2 hours for a 10-year simulation period. Choose a larger spatial resolution to reduce model run-time.', QgsMessageBar.WARNING, 10)
        elif self.cells > 2000000:
            iface.messageBar().pushMessage('Warning:', 'Your model has > 2,000,000 cells. This means that model run-time will be very long!! Choose a larger spatial resolution to reduce model run-time.', QgsMessageBar.WARNING, 10)
        self.area = (self.cells * self.spatialRes**2) / 1000000  # to km2
        rectangle.setXMinimum(self.xMin)
        rectangle.setXMaximum(self.xMax)
        rectangle.setYMinimum(self.yMin)
        rectangle.setYMaximum(self.yMax)
        return rectangle
    
    #-Transform point from one coordinate system to the other    
    def coordinateTransform(self, s_srs, t_srs, rectangle):
        xform = QgsCoordinateTransform(s_srs, t_srs)
        try:
            rectangle = xform.transform(rectangle)
        except:
            rectangle = None
        #-This function can result in strange coordinates is the rectangle is dragged for a different utm area than is specified
        return rectangle
    
    #-Re-create the selected area based on the user defined resolution
    def recreateArea(self):
        #-Get the spatial resolution from the GUI
        self.spatialRes = self.spatialResolutionSpinBox.value()
        #-Define the rectangle
        rectangle = QgsRectangle(self.xMin, self.yMin, self.xMax, self.yMax)
        #-calculate correct extent properties
        rectangle = self.calculateExtent(rectangle)
        #-Transform the coordinates of the correct rectangle extent to the mapCrs for drawing
        rectangleMapCrs = self.coordinateTransform(self.userCRS, self.mapCrs, rectangle)
        #-delete the current area
        self.deleteSelectedArea()
        #-Create polygone shapefile of the selected area
        self.createPolygonArea(rectangleMapCrs)
        #-Update the config file
        self.updateAreaConfig()
        self.saveProject()

    #-Function that creates a clone from the given extent
    def createClone(self):
        if os.path.isfile(self.resultsPath + 'clone.map'):
            #-check if file exists in map canvas and if so, then remove
            for l in iface.mapCanvas().layers():
                if l.source() == self.clone:
                    QgsMapLayerRegistry.instance().removeMapLayer(l.id()) 
                    #QgsVectorFileWriter.deleteShapeFile(l.source())
            os.remove(self.resultsPath + 'clone.map')
        #-Command for creating new clone
        command = ('mapattr -s -R ' + str(int(self.rows)) + ' -C ' + str(int(self.cols)) + ' -P yb2t -B -x '\
                         + str(int(self.xMin)) + ' -y ' + str(int(self.yMax)) + ' -l ' + str(self.spatialRes)\
                         + ' ' + self.resultsPath + 'clone.map')
        #command = self.pcrasterBatchFile(command)
        subprocess.Popen(command, env={"PATH": self.pcrBinPath}, shell=True).wait()
        #-Check if clone was succesfully created
        if os.path.isfile(self.resultsPath + 'clone.map'):
            iface.messageBar().pushMessage('Info:', 'Clone map was successfully created.', QgsMessageBar.INFO, 10)
            self.updateConfig('AREA', 'clone_grid', self.resultsPath + 'clone.map')
        else:
            iface.messageBar().pushMessage('Error:', 'Clone map was NOT created!', QgsMessageBar.CRITICAL, 10)
            self.updateConfig('AREA', 'clone_grid', '')
        self.saveProject()
        
    #-Function to update the modules
    def updateModules(self, state):
        sender = self.sender().objectName()
        module = self.configModulesDict[sender][0]
        par = self.configModulesDict[sender][1]
        if state == QtCore.Qt.Unchecked:
            self.updateConfig(module, par, 0)
        else:
            self.updateConfig(module, par, 1)
        self.saveProject()
        
    #-Function that creates the initial maps based on the selected modules
    def createInitMaps(self):
        #-clear the process log text widget
        self.processLog1TextEdit.clear()   
        # maps to process to define progress in progressbar
        maps = 13
        mm = 0.
        # recreate clone -> is necessary if initial maps are created again after delineation has been performed
        # because delineation results in a smaller clone -> clipped to basin
        self.createClone()
        mm+=1
        self.initialMapsProgressBar.setValue(mm/maps*100)
        if self.routing:
            maps += 5
        if self.glacier:
            maps += 3
        #-set the map properties of resulting maps
        t_srs =  self.userCRS.authid()
        res = self.spatialRes
        extent = '-te ' + str(self.xMin) + ' ' + str(self.yMin) + ' ' + str(self.xMax) + ' ' + str(self.yMax)
        #-delete old raster layers from canvas and disk if exists
        for k in self.generalMaps:
            self.deleteLayer(os.path.join(self.resultsPath, self.generalMaps[k]), 'raster')
        for k in self.routingMaps:
            self.deleteLayer(os.path.join(self.resultsPath, self.routingMaps[k]), 'raster')
        for k in self.glacierMaps:
            self.deleteLayer(os.path.join(self.resultsPath, self.glacierMaps[k]), 'raster')

        #-remove temporary tiffs from results dir
        fi = glob.glob(os.path.join(self.resultsPath, 'temp*'))
        for f in fi:
            os.remove(f)
        
        ### First make the DEM ####################################
        #-create the commands to execute
        commands = []
        infile = os.path.join(self.databasePath, self.databaseConfig.get('DEM', 'file'))
        outfile = os.path.join(self.resultsPath, 'temp.tif')
        s_srs = 'EPSG:' + self.databaseConfig.get('DEM', 'EPSG')
        #-Create a class with the gdal methods
        m = SpatialProcessing(infile, outfile, s_srs, t_srs, res, extra=extent)
        #-Project, clip and resample
        commands.append(m.reproject())
        #-Convert to PCRaster map
        m.extra = '-of PCRaster'
        m.input = m.output
        m.output = os.path.join(self.resultsPath, self.generalMaps['DEM'])
        commands.append(m.rasterTranslate())
        #-Execute the command(s) in a thread
        self.threadWorker(SubProcessWorker(commands, self.processLog1TextEdit, 'DEM', self.generalMaps['DEM'], True, 'raster'))
        while self.thread.isRunning(): #-wait till the thread is finished before continue
            print ''
        #-set progress bar value
        mm+=1
        self.initialMapsProgressBar.setValue(mm/maps*100)
        ### make the Slope #######
        command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.generalMaps['Slope']) + '"'\
                                        + ' = slope(' + '"' + os.path.join(self.resultsPath, self.generalMaps['DEM']) + '"' + ')')
        self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'Slope', self.generalMaps['Slope'], True, 'raster', env={"PATH": self.pcrBinPath}))
        while self.thread.isRunning(): #-wait till the thread is finished before continue
            print ''
        #-remove temporary tiffs from results dir
        fi = glob.glob(os.path.join(self.resultsPath, 'temp.*'))
        for f in fi:
            os.remove(f)
        #-set progress bar value
        mm+=1
        self.initialMapsProgressBar.setValue(mm/maps*100)
        ### Latitude map ##############################################
        #-create the commands to execute
        commands = []
        infile = os.path.join(self.databasePath, self.databaseConfig.get('LATITUDE', 'file'))
        outfile = os.path.join(self.resultsPath, 'temp.tif')
        s_srs = 'EPSG:' + self.databaseConfig.get('LATITUDE', 'EPSG')
        #-Create a class with the gdal methods
        m = SpatialProcessing(infile, outfile, s_srs, t_srs, res, extra=extent)
        #-Project, clip and resample
        commands.append(m.reproject())
        #-Convert to PCRaster map
        m.extra = '-of PCRaster'
        m.input = m.output
        m.output = os.path.join(self.resultsPath, self.generalMaps['Latitudes'])
        commands.append(m.rasterTranslate())
        #-Execute the command(s) in a thread
        self.threadWorker(SubProcessWorker(commands, self.processLog1TextEdit, 'Latitudes', self.generalMaps['Latitudes'], True, 'raster'))
        while self.thread.isRunning(): #-wait till the thread is finished before continue
            print ''
        #-remove temporary tiffs from results dir
        fi = glob.glob(os.path.join(self.resultsPath, 'temp.*'))
        for f in fi:
            os.remove(f)
        #-set progress bar value
        mm+=1
        self.initialMapsProgressBar.setValue(mm/maps*100)
        ### Landuse map ##############################################
        #-create the commands to execute
        commands = []
        infile = os.path.join(self.databasePath, self.databaseConfig.get('LANDUSE', 'file'))
        outfile = os.path.join(self.resultsPath, 'temp.tif')
        s_srs = 'EPSG:' + self.databaseConfig.get('LANDUSE', 'EPSG')
        #-Create a class with the gdal methods
        m = SpatialProcessing(infile, outfile, s_srs, t_srs, res, resampling='mode', rtype='Int32', extra=extent)
        #-Project, clip and resample
        commands.append(m.reproject())
        #-Convert to PCRaster map
        m.extra = '-of PCRaster'
        m.input = m.output
        m.output = os.path.join(self.resultsPath, self.generalMaps['LandUse'])
        commands.append(m.rasterTranslate())
        #-Execute the command(s) in a thread
        self.threadWorker(SubProcessWorker(commands, self.processLog1TextEdit, 'LandUse', self.generalMaps['LandUse'], True, 'raster'))
        while self.thread.isRunning(): #-wait till the thread is finished before continue
            print ''
        #-remove temporary tiffs from results dir
        fi = glob.glob(os.path.join(self.resultsPath, 'temp.*'))
        for f in fi:
            os.remove(f)
        #-set progress bar value
        mm+=1
        self.initialMapsProgressBar.setValue(mm/maps*100)
        #### Soil maps ###################################################
        soilMapTiffs = {'Root_field': 'root_field_file', 'Root_sat': 'root_sat_file', 'Root_dry': 'root_dry_file'\
                        ,'Root_wilt': 'root_wilt_file', 'Root_Ksat': 'root_ksat_file', 'Sub_field': 'sub_field_file'\
                        ,'Sub_sat': 'sub_sat_file', 'Sub_Ksat': 'sub_ksat_file'}
        for smap in soilMapTiffs:
            #-create the commands to execute
            commands = []
            infile = os.path.join(self.databasePath, self.databaseConfig.get('SOIL', soilMapTiffs[smap]))
            outfile = os.path.join(self.resultsPath, 'temp.tif')
            s_srs = 'EPSG:' + self.databaseConfig.get('SOIL', 'EPSG')
            #-Create a class with the gdal methods
            m = SpatialProcessing(infile, outfile, s_srs, t_srs, res, extra=extent)
            #-Project, clip and resample
            commands.append(m.reproject())
            #-Convert to PCRaster map
            m.extra = '-of PCRaster'
            m.input = m.output
            m.output = os.path.join(self.resultsPath, self.generalMaps[smap])
            commands.append(m.rasterTranslate())
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(commands, self.processLog1TextEdit, smap, self.generalMaps[smap], True, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-remove temporary tiffs from results dir
            fi = glob.glob(os.path.join(self.resultsPath, 'temp.*'))
            for f in fi:
                os.remove(f)
            #-set progress bar value
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
        ############### ROUTING MAPS, IF MODULE IS ON ##########################
        if self.currentConfig.getint('MODULES', 'routing') == 1:
            #-delete old raster layers from canvas and disk if exists
#             for k in self.routingMaps:
#                 try:
#                     self.deleteLayer(os.path.join(self.resultsPath, self.routingMaps[k]), 'raster')
#                 except:
#                     pass
            
            ### LDD map #######
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"'\
                            + ' = lddcreate(' + '"' + os.path.join(self.resultsPath, self.generalMaps['DEM']) + '"' + ', 1e31, 1e31, 1e31, 1e31)')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'LDD', self.routingMaps['LDD'], False, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            ### LDD REPAIRED map ######
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"'\
                            + ' = lddrepair(' + '"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"' + ')')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'LDD', self.routingMaps['LDD'], False, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-set progress bar value
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
            ###### Accuflux map #############
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['AccuFlux']) + '"'\
                            + ' = accuflux(' + '"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"' + ',1)')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'AccuFlux', self.routingMaps['AccuFlux'], True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-set progress bar value
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
            ####### Rivers map ########## assumed >50 cells
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['Rivers']) + '"'\
                            + ' = ' + '"' + os.path.join(self.resultsPath, self.routingMaps['AccuFlux']) + '"' + '> 50')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'Rivers', self.routingMaps['Rivers'], True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
            ######## Outlets #########  is initially the pits in the ldd (later the user can specify outlets)
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['Outlets']) + '"'\
                            + ' = pit(' + '"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"' + ')')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'Outlets', self.routingMaps['Outlets'], True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
            ######## Sub-basins #########  is initially based on outlets (pits in the ldd (later the user can specify outlets and re-create sub-basins))
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['Sub-basins']) + '"'\
                            + ' = subcatchment(' + '"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"' + ',' + '"' + os.path.join(self.resultsPath, self.routingMaps['Outlets']) + '"' + ')')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'Sub-basins', self.routingMaps['Sub-basins'], True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
                
        ################### GLACIER MAPS, IF MODULE IS ON ########################
        if self.currentConfig.getint('MODULES', 'glacier') == 1:
            #-delete old raster layers from canvas and disk if exists
#             for k in self.glacierMaps:
#                 try:
#                     self.deleteLayer(os.path.join(self.resultsPath, self.glacierMaps[k]), 'raster')
#                 except:
#                     pass
            infile = os.path.join(self.databasePath, self.databaseConfig.get('GLACIER', 'file'))
            outfile = os.path.join(self.resultsPath, 'temp.shp')
            s_srs = 'EPSG:' + self.databaseConfig.get('GLACIER', 'EPSG')
            #-Project the layer to the user CRS
            processing.runalg("qgis:reprojectlayer", infile, t_srs, outfile)
            ########-Create gridded glacier outlines
            infile = outfile
            outfile = os.path.join(self.resultsPath, 'temp.tif')
            #-Create a class with the gdal methods
            m = SpatialProcessing(infile, outfile, s_srs, t_srs, res/10, extra='-a_nodata -3.40282e+38 -burn 1.0 ' + extent)
            self.threadWorker(SubProcessWorker([m.rasterize()], self.processLog1TextEdit, 'Gridded Randolph', outfile, True, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
#             ########-Reclassify (NaN->0)  -> Uncertain why this is not working correctly. Therefore commented and part below is used.
#             infile = outfile
#             outfile = os.path.join(self.resultsPath, 'temp2.tif')
#             processing.runalg("saga:reclassifygridvalues", infile, 0, 0, 0, 0, 0, 1, 2, 0, "0,0,0,0,0,0,0,0,0", 0, \
#                 True, 0, False, 0, outfile)
#             self.addCanvasLayer(outfile, 'temp2', 'raster')
#             return
            ########-Reclassify (NaN->0)
            #-First convert to pcraster map
            m.input = outfile
            m.output = os.path.join(self.resultsPath, 'temp.map')
            m.extra = '-of PCRaster'
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker([m.rasterTranslate()], self.processLog1TextEdit, 'temp', m.output, False, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-Replace NaN with zero
            infile = m.output
            outfile = os.path.join(self.resultsPath, 'temp2.map')
            command = self.pcrasterModelFile('"' + outfile + '" = cover("' + infile + '", 0)')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'temp2', outfile, False, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-Convert to Geotiff
            m.input = outfile
            m.output = os.path.join(self.resultsPath, 'temp2.tif')
            m.extra = ''
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker([m.rasterTranslate()], self.processLog1TextEdit, 'temp2', m.output, True, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #######-Aggregate the results to the glacier fraction map
            infile = m.output
            outfile = os.path.join(self.resultsPath, 'temp3.tif')
            extent = str(self.xMin) + "," +  str(self.xMax) +"," + str(self.yMin) + "," + str(self.yMax)
            processing.runalg("grass:r.resamp.stats", infile, 0, False, False, False, extent, res, outfile)
            self.addCanvasLayer(outfile, 'temp3', 'raster')
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
            ######-Convert to PCRaster map
            m.input = outfile
            m.output = os.path.join(self.resultsPath, self.glacierMaps['GlacFrac'])
            m.extra = '-of PCRaster'
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker([m.rasterTranslate()], self.processLog1TextEdit, 'GlacFrac', m.output, True, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            ########-Debris fraction map
            demfile = os.path.join(self.resultsPath, self.generalMaps['DEM'])
            slopefile = os.path.join(self.resultsPath, self.generalMaps['Slope'])
            glacfracfile = os.path.join(self.resultsPath, self.glacierMaps['GlacFrac'])
            outfile = os.path.join(self.resultsPath, self.glacierMaps['GlacFracDB'])
            command = self.pcrasterModelFile('"' + outfile + '"' + ' = scalar(if("' + demfile + '" lt 4100 and scalar(atan("' + slopefile + '")) lt 24 and "' + glacfracfile + '" gt 0, 1, 0))')
            #command = self.pcrasterModelFile('"' + outfile + '"' + ' = scalar(if("' + demfile + '" lt 4100 and scalar(atan("' + slopefile + '")) lt 24, 1, 0))')
            #-Execute the command(s) in a thread            
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'GlacFracDB', self.glacierMaps['GlacFracDB'], True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
            ########-Clean ice fraction map
            infile = outfile
            outfile = os.path.join(self.resultsPath, self.glacierMaps['GlacFracCI'])
            command = self.pcrasterModelFile('"' + outfile + '"' + ' = scalar(if("' + infile + '" eq 0 and "' + glacfracfile + '" gt 0, 1, 0))')
            #-Execute the command(s) in a thread            
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog1TextEdit, 'GlacFracCI', self.glacierMaps['GlacFracCI'], True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            mm+=1
            self.initialMapsProgressBar.setValue(mm/maps*100)
       
        self.initialMapsProgressBar.setValue(100.0)
        time.sleep(1)
        self.processLog1TextEdit.append('Processing is finished')
        self.initialMapsProgressBar.setValue(0.)
        #-Try to delete the temporary layers from canvas and disk
        self.deleteLayer(os.path.join(self.resultsPath, 'temp.shp'), 'shape')
        self.deleteLayer(os.path.join(self.resultsPath, 'temp.tif'), 'raster')
        self.deleteLayer(os.path.join(self.resultsPath, 'temp2.tif'), 'raster')
        self.deleteLayer(os.path.join(self.resultsPath, 'temp3.tif'), 'raster')
        #-Activate the delineation button in the "Basin delineation" Tab
        self.delineateButton.setEnabled(1)
        
        
    #-Function to update the basin delineation settings        
    def updateDelineation(self, state):
        sender = self.sender().objectName()
        if sender == 'selectOutletsButton':
            outlets = QtGui.QFileDialog.getOpenFileName(self, "Select the Outlet(s) shapefile", self.resultsPath, "outlets.shp")
            if outlets:
                self.deleteLayer(outlets, 'shape', remLayerDisk=False)
                self.outletsShp = outlets
                self.updateConfig('DELINEATION', 'outlets_shp', outlets)
                self.addCanvasLayer(outlets, 'Outlet(s)', 'shape')
        else:
            module = self.configDict[sender][0]
            par = self.configDict[sender][1]
            if state == QtCore.Qt.Unchecked:
                self.updateConfig(module, par, 0)
            else:
                self.updateConfig(module, par, 1)
        self.saveProject()
        self.Tab.setCurrentIndex(3)
        
    #-Function to Delineate basin and create sub-basins and clipped maps
    def delineate(self):
        self.processLog2TextEdit.clear()
        #-Check if outlet(s) shapefile and ldd are present
        if self.outletsShp and os.path.isfile(os.path.join(self.resultsPath, self.routingMaps['LDD'])):
            #-set counters for progress bar
            mm = 0.
            maps = 2 # basin and outlets
            if self.currentConfig.getint('DELINEATION', 'subbasins') == 1:
                maps += 1
            if self.currentConfig.getint('DELINEATION', 'clip') == 1:
                maps += len(self.generalMaps) + len(self.routingMaps) + 2
            if self.glacier:
                maps += len(self.glacierMaps)
            #-delete old raster layers from canvas
            for k in self.routingMaps:
                    self.deleteLayer(os.path.join(self.resultsPath, self.routingMaps[k]), 'raster', remLayerDisk=False)
            #-delete basin map if exists
            self.deleteLayer(os.path.join(self.resultsPath, 'basin.map'), 'raster')
            #####-First convert outlet(s) shapefile to GeoTiff
            extent = str(self.xMin) + ',' + str(self.xMax) + ',' + str(self.yMin) + ',' + str(self.yMax)
            outfile = os.path.join(self.resultsPath, 'temp.tif')
            self.processLog2TextEdit.append('Converting Outlet(s) to raster...')
            processing.runalg("grass:v.to.rast.attribute", self.outletsShp, 0, "id", extent, self.spatialRes, -1.0, 0.0001, outfile)
            #####-Translate GeoTiff to PCRaster map
            infile = outfile
            outfile = os.path.join(self.resultsPath, 'temp.map')
            #-Create a class with the gdal methods
            m = SpatialProcessing(infile, outfile, None, None, None, extra='-of PCRaster -ot Float32')
            #-Execute the command(s) in a thread            
            self.threadWorker(SubProcessWorker([m.rasterTranslate()], self.processLog2TextEdit, 'temp', outfile, False, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-Convert to nominal PCRaster map
            infile = outfile 
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['Outlets']) + '"'\
                + ' = nominal("' + infile + '")')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, 'Outlets', self.routingMaps['Outlets'], False, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-update progressbar
            mm += 1
            self.delineateProgressBar.setValue(mm/maps*100)
            self.processLog2TextEdit.append('Delineating basin...')
            #-Delineate the basin based on the ldd and the defined outlets
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, 'basin.map') + '"'\
                + ' = catchment("' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '","' + os.path.join(self.resultsPath, self.routingMaps['Outlets']) + '")')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, 'Basin', 'basin.map', False, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-update progressbar
            mm += 1
            self.delineateProgressBar.setValue(mm/maps*100)
            #-Create sub-basins
            if self.currentConfig.getint('DELINEATION', 'subbasins') == 1:
                self.processLog2TextEdit.append('Creating sub-basins...')
                command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['Sub-basins']) + '"'\
                    + ' = subcatchment("' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '","' + os.path.join(self.resultsPath, self.routingMaps['Outlets']) + '")')
                #-Execute the command(s) in a thread
                self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, 'Sub-basins', self.routingMaps['Sub-basins'], False, 'raster', env={"PATH": self.pcrBinPath}))
                while self.thread.isRunning(): #-wait till the thread is finished before continue
                    print ''
                #-update progressbar
                mm += 1
                self.delineateProgressBar.setValue(mm/maps*100)
                    
            #-Clip all the maps to the basin outline 
            if self.currentConfig.getint('DELINEATION', 'clip') == 1:
                self.processLog2TextEdit.append('Clipping maps to basin outline...')
                
                #-re-create the clone, based on the delineated basin map
                command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, 'clone.map') + '"'\
                    + ' = boolean("' + os.path.join(self.resultsPath, 'basin.map') +  '")')
                #-Execute the command(s) in a thread
                self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, 'Clone', 'clone.map', False, 'raster', env={"PATH": self.pcrBinPath}))
                while self.thread.isRunning(): #-wait till the thread is finished before continue
                    print ''
                #-update progressbar
                mm += 1
                self.delineateProgressBar.setValue(mm/maps*100)
                #-delete old general raster layers from canvas and clip general maps to basin outline and add to canvas
                for k in self.generalMaps:
                    self.deleteLayer(os.path.join(self.resultsPath, self.generalMaps[k]), 'raster', remLayerDisk=False)
                    command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.generalMaps[k]) + '"'\
                        + ' = if("' + os.path.join(self.resultsPath, 'clone.map') + '","' + os.path.join(self.resultsPath, self.generalMaps[k]) +  '")')
                    #-Execute the command(s) in a thread
                    self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, k, self.generalMaps[k], True, 'raster', env={"PATH": self.pcrBinPath}))
                    while self.thread.isRunning(): #-wait till the thread is finished before continue
                        print ''
                    #-update progressbar
                    mm += 1
                    self.delineateProgressBar.setValue(mm/maps*100)
                         
                #-delete old glacier raster layers from canvas and clip glacier maps to basin outline and add to canvas
                if self.glacier: 
                    for k in self.glacierMaps:
                        self.deleteLayer(os.path.join(self.resultsPath, self.glacierMaps[k]), 'raster', remLayerDisk=False)
                        command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.glacierMaps[k]) + '"'\
                            + ' = if("' + os.path.join(self.resultsPath, 'clone.map') + '","' + os.path.join(self.resultsPath, self.glacierMaps[k]) +  '")')
                        #-Execute the command(s) in a thread
                        self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, k, self.glacierMaps[k], True, 'raster', env={"PATH": self.pcrBinPath}))
                        while self.thread.isRunning(): #-wait till the thread is finished before continue
                            print ''
                        #-update progressbar
                        mm += 1
                        self.delineateProgressBar.setValue(mm/maps*100)
                #-delete old routing raster layers from canvas and clip routing maps to basin outline and add to canvas
                for k in self.routingMaps:
                    command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps[k]) + '"'\
                        + ' = if("' + os.path.join(self.resultsPath, 'clone.map') + '","' + os.path.join(self.resultsPath, self.routingMaps[k]) +  '")')
                    #-Execute the command(s) in a thread
                    self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, k, self.routingMaps[k], True, 'raster', env={"PATH": self.pcrBinPath}))
                    while self.thread.isRunning(): #-wait till the thread is finished before continue
                        print ''
                    #-update progressbar
                    mm += 1
                    self.delineateProgressBar.setValue(mm/maps*100)    
                
                #-repair ldd, because clipping may result in unsound ldd
                self.deleteLayer(os.path.join(self.resultsPath, self.routingMaps['LDD']), 'raster', remLayerDisk=False)
                command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"'\
                            + ' = lddrepair(' + '"' + os.path.join(self.resultsPath, self.routingMaps['LDD']) + '"' + ')')
                #-Execute the command(s) in a thread
                self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, 'LDD', self.routingMaps['LDD'], True, 'raster', env={"PATH": self.pcrBinPath}))
                while self.thread.isRunning(): #-wait till the thread is finished before continue
                    print ''
                
                #-Clip basin map to outline
                command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, 'basin.map') + '"'\
                    + ' = if("' + os.path.join(self.resultsPath, 'clone.map') + '","' + os.path.join(self.resultsPath, 'basin.map') +  '")')
                #-Execute the command(s) in a thread
                self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog2TextEdit, 'Basin', 'basin.map', True, 'raster', env={"PATH": self.pcrBinPath}))
                while self.thread.isRunning(): #-wait till the thread is finished before continue
                    print ''
                #-update progressbar
                mm += 1
                self.delineateProgressBar.setValue(mm/maps*100)
                #-Disable the delineation button if clipped option is used, because otherwise errors occur in output maps if clicked again.
                #-Therefore the modules section (create initial maps) needs to be run first again in order to re-activate this button
                self.delineateButton.setEnabled(0)
            
            #-if no clipping, then add the routing maps again to the mapcanvas
            else:
                for k in self.routingMaps:
                    self.addCanvasLayer(os.path.join(self.resultsPath, self.routingMaps[k]), k, 'raster')
                self.addCanvasLayer(os.path.join(self.resultsPath, 'basin.map'), 'Basin', 'raster')
                if self.currentConfig.getint('DELINEATION', 'subbasins') == 0:
                    self.deleteLayer(os.path.join(self.resultsPath, 'subbasins.map'), 'raster')
                
            self.delineateProgressBar.setValue(100.0)
            time.sleep(1)
            self.processLog2TextEdit.append('Basin delineation finished.')
            self.delineateProgressBar.setValue(0.)
   
        else:
            self.processLog2TextEdit.append('Error: missing outlets.shp and/or ldd.map in output folder.')
        #-remove temporary tiffs from results dir
        fi = glob.glob(os.path.join(self.resultsPath, 'temp*'))
        for f in fi:
            os.remove(f)
    
    #-Function to update the station settings
    def updateStations(self):
        stations = QtGui.QFileDialog.getOpenFileName(self, "Select the station(s) shapefile", self.resultsPath, "stations.shp")
        if stations:
            self.deleteLayer(stations, 'shape', remLayerDisk=False)
            self.stationsShp = stations
            self.updateConfig('STATIONS', 'stations_shp', stations)
            self.addCanvasLayer(stations, 'Station(s)', 'shape')
        self.saveProject()
        self.Tab.setCurrentIndex(4)

    #-Function to convert stations.shp to pcraster nominal map with stations        
    def createStations(self):
        self.processLog3TextEdit.clear()
        #-Check if station(s) shapefile is present
        if self.stationsShp:
            #-delete stations map if exists
            self.deleteLayer(os.path.join(self.resultsPath, 'stations.map'), 'raster')
            #####-First convert station(s) shapefile to GeoTiff
            extent = str(self.xMin) + ',' + str(self.xMax) + ',' + str(self.yMin) + ',' + str(self.yMax)
            outfile = os.path.join(self.resultsPath, 'temp.tif')
            self.processLog3TextEdit.append('Converting Station(s) to raster...')
            processing.runalg("grass:v.to.rast.attribute", self.stationsShp, 0, "id", extent, self.spatialRes, -1.0, 0.0001, outfile)
            #####-Translate GeoTiff to PCRaster map
            infile = outfile
            outfile = os.path.join(self.resultsPath, 'temp.map')
            #-Create a class with the gdal methods
            m = SpatialProcessing(infile, outfile, None, None, None, extra='-of PCRaster -ot Float32')
            #-Execute the command(s) in a thread            
            self.threadWorker(SubProcessWorker([m.rasterTranslate()], self.processLog3TextEdit, 'temp', outfile, False, 'raster'))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
            #-Convert to nominal PCRaster map
            infile = outfile 
            command = self.pcrasterModelFile('"' + os.path.join(self.resultsPath, 'stations.map') + '"'\
                + ' = nominal("' + infile + '")')
            #-Execute the command(s) in a thread
            self.threadWorker(SubProcessWorker(['pcrcalc -f ' + command], self.processLog3TextEdit, 'Stations', 'stations.map', True, 'raster', env={"PATH": self.pcrBinPath}))
            while self.thread.isRunning(): #-wait till the thread is finished before continue
                print ''
                
                
            self.processLog3TextEdit.append('Station creation finished.')
        else:
            self.processLog3TextEdit.append('Error: missing stations.shp in output folder.')
        #-remove temporary tiffs from results dir
        fi = glob.glob(os.path.join(self.resultsPath, 'temp*'))
        for f in fi:
            os.remove(f)
    
    #-Function to update the forcing settings
    def updateForcing(self, state):
        sender = self.sender()
        senderName = sender.objectName()
        var = senderName[0:4]
        key = senderName.split(var)[1]
        #-If the select csv buttons are clicked
        if isinstance(sender, QtGui.QToolButton): 
            toolDict = {'LocToolButton': 'LocFile', 'DataToolButton': 'DataFile'}
            pars = var + toolDict[key]
            if key[0:3] == 'Loc':
                f = QtGui.QFileDialog.getOpenFileName(self, "Select the location csv file", self.resultsPath, "*.csv")
            else:
                f = QtGui.QFileDialog.getOpenFileName(self, "Select the data csv file", self.resultsPath, "*.csv")
            if os.path.isfile(f):
                self.updateConfig('FORCING', pars, f)
        #-Otherwise update the checkbox or radiobutton values in the config file
        else:
            pars = var + self.forcingDict[key]
            if isinstance(sender, QtGui.QCheckBox):
                if state == QtCore.Qt.Checked:
                    self.updateConfig('FORCING', var + 'FLAG', 1)
                else:
                    self.updateConfig('FORCING', var + 'FLAG', 0)
            else:
                if state:
                    self.updateConfig('FORCING', var + 'DB', 1)
                else:
                    self.updateConfig('FORCING', var + 'DB', 0)
        self.saveProject()
        
    def createForcing(self):
        self.processLog4TextEdit.clear()
        #-settings for progressbar
        timeSteps = ((self.enddate-self.startdate).days + 1)
        procSteps = 0.
        if self.precFLAG:
            procSteps+=timeSteps
        if self.tempFLAG:
            procSteps+=(timeSteps * 3)
        #-Create instance of processForcing
        f = processForcing(self.resultsPath, self.userCRS.authid(), self.spatialRes, [self.xMin, self.yMin, self.xMax, self.yMax],\
            self.startdate, self.enddate, self.processLog4TextEdit, self.forcingProgressBar, procSteps, self.pcrBinPath)
        if self.precDB or self.tempDB:
            #-Database properties
            f.dbSource = self.databaseConfig.get('METEO', 'source')
            f.dbTs = self.databaseConfig.get('METEO', 'file_timestep')
            f.dbSrs = 'EPSG:' + self.databaseConfig.get('METEO', 'EPSG')
            f.dbFormat = self.databaseConfig.get('METEO', 'format')
        if self.precFLAG:
            if self.precDB:
                f.precDBPath = os.path.join(self.databasePath, self.databaseConfig.get('METEO', 'prec_folder'))
                f.createPrecDB() 
            else:
                f.precLocFile = self.precLocFile
                f.precDataFile = self.precDataFile 
                f.createPrecCSV()
        if self.tempFLAG:
            if self.tempDB:
                f.tavgDBPath = os.path.join(self.databasePath, self.databaseConfig.get('METEO', 'tavg_folder'))
                f.tmaxDBPath = os.path.join(self.databasePath, self.databaseConfig.get('METEO', 'tmax_folder'))
                f.tminDBPath = os.path.join(self.databasePath, self.databaseConfig.get('METEO', 'tmin_folder'))
                f.dbDem = os.path.join(self.databasePath, self.databaseConfig.get('METEO', 'dem'))
                f.modelDem = os.path.join(self.resultsPath, self.generalMaps['DEM'])
                f.createTempDB() 
            else:
                f.tempLocFile = self.tempLocFile
                f.tempDataFile = self.tempDataFile 
                f.createTempCSV()
        if not self.precFLAG and not self.tempFLAG:
            self.processLog4TextEdit.append('Nothing to process.')
        self.forcingProgressBar.setValue(100)
        time.sleep(.5)
        self.forcingProgressBar.setValue(0)
        

    #-Start the worker in a thread
    def threadWorker(self, worker):
        # start the worker in a new thread
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        # listeners        
        worker.finished.connect(self.workerFinished)
        worker.error.connect(self.workerError)
        worker.cmdProgress.connect(self.workerListener)
        thread.started.connect(worker.run)
        thread.start()
        self.thread = thread
        self.worker = worker
     
    def workerFinished(self, result):
        # clean up the worker and thread
        self.thread.quit()
        self.thread.wait()
        # check the process
        process = result[0]
        # map name
        mapName = result[1]
        # file name
        fileName = result[2]
        # check if map has to be added to canvas
        addMap = result[3]
        # check file type
        fType = result[4]
        # textlog
        textLog = result[5]
        if process is None and mapName:
            textLog.append(mapName + ' map was not created.')
        elif process and mapName:
            textLog.append(mapName + ' was created succesfully.')
            if addMap: #-check if map has to be added to canvas
                self.addCanvasLayer(os.path.join(self.resultsPath, fileName), mapName, fType)
 
    # function that is launched whenever the model is unable to run            
    def workerError(self, e, exception_string):
        QgsMessageLog.logMessage('Worker thread raised an exception:\n'.format(exception_string), level=QgsMessageLog.CRITICAL)
         
    # function that parses cmd line output to the text widget    
    def workerListener(self, result):
        line = result[0]
        textLog = result[1]
        textLog.append(line)

    #-Function that creates a pcraster model file (*.mod)
    def pcrasterModelFile(self, command):
        #-Create a batch file
        batchfile = os.path.dirname(__file__) + '/pcraster/run.mod'
        f = open(batchfile, "w")
        f.write(command)
        f.close()
        return batchfile
        
    #-Change user specified CRS if spinbox value is changed or radio button is toggled
    def changeCRS(self, enabled):
        sender = self.sender().objectName()
        widget = eval('self.' + sender)
        if isinstance(widget, QtGui.QSpinBox):
            value = widget.value()
            self.updateConfig('GENERAL', 'utmZoneNr', value)
        else: #-then it is a radio button
            if sender == "utmNRadioButton" and enabled:
                self.updateConfig('GENERAL', 'utmZoneStr', 'N')
            else:
                self.updateConfig('GENERAL', 'utmZoneStr', 'S')
        crs = self.lookupUTM(self.currentConfig.getint('GENERAL', 'utmZoneNr'), self.currentConfig.get('GENERAL', 'utmZoneStr'))
        self.userCRS = QgsCoordinateReferenceSystem("EPSG:" + str(crs))
        self.saveProject()  #-if this causes hanging, then remove this line

    #-function to lookup the WGS84 / UTM zone from the sqlite database in QGIS
    def lookupUTM(self, utmNr, utmStr):
        con = sqlite3.connect(QgsApplication.srsDbFilePath())
        cur = con.cursor()
        cur.execute("select * from tbl_srs WHERE description = 'WGS 84 / UTM zone %s%s'" %(str(utmNr), utmStr))
        epsg = cur.fetchone()
        cur.close()
        con.close()
        #-return the EPSG code
        return epsg[5]

    # validate start and enddate and set in config        
    def updateDate(self):
#         if self.exitDate: # don't execute this function if GUI is initialized during new project or open project creation.
#             return
        # validate if simulation settings are ok
        startdate = self.startDateEdit.date()
        enddate = self.endDateEdit.date()
        if startdate >= enddate:
            QtGui.QMessageBox.warning(self, "Date error", "End date should be larger than start date")
            if self.sender().objectName() == "startDateEdit":
                enddate = QtCore.QDate(startdate).addDays(1)
                self.endDateEdit.setDate(enddate)
            else:
                startdate = QtCore.QDate(enddate).addDays(-1)
                self.startDateEdit.setDate(startdate)
        self.updateConfig("GENERAL", "startyear", QtCore.QDate.year(startdate))
        self.updateConfig("GENERAL", "startmonth", QtCore.QDate.month(startdate))
        self.updateConfig("GENERAL", "startday", QtCore.QDate.day(startdate))
        self.updateConfig("GENERAL", "endyear", QtCore.QDate.year(enddate))
        self.updateConfig("GENERAL", "endmonth", QtCore.QDate.month(enddate))
        self.updateConfig("GENERAL", "endday", QtCore.QDate.day(enddate))
        self.saveProject()
        
    ############### DELETE AND ADD LAYERS FROM CANVAS AND DISK #############
    
    #-Add a shapefile or raster layer to the canvas (specified by name and type = vector or raster)
    def addCanvasLayer(self, filename, name, ftype):
        if ftype == 'raster':
            layer = QgsRasterLayer(filename, name)
            layer.setCrs(QgsCoordinateReferenceSystem(self.userCRS))
            QgsMapLayerRegistry.instance().addMapLayer(layer, False)
            root = QgsProject.instance().layerTreeRoot()
            root.insertLayer(0, layer)
            self.rasterSymbology(filename, layer)
            iface.messageBar().popWidget() #   -> uncomment to remove epsg crs warning
        elif ftype == 'shape':
            layer = QgsVectorLayer(filename, name, 'ogr')
            #layer.setCrs(QgsCoordinateReferenceSystem(self.userCRS))
            QgsMapLayerRegistry.instance().addMapLayer(layer, False)
            root = QgsProject.instance().layerTreeRoot()
            root.insertLayer(0, layer)
        
    #-Delete a shapefile or raster layer from the canvas and disk (specified by name and type = vector or raster)
    def deleteLayer(self, filename, ftype, remLayerDisk=True):
#         for l in iface.mapCanvas().layers():
        for l in iface.legendInterface().layers():            
            print l.name()
            if l.source() == filename:
                QgsMapLayerRegistry.instance().removeMapLayer(l.id())
                print filename + ' removed'
                break
        #-Check if file should also be removed from disk
        if os.path.isfile(filename) and remLayerDisk:
            if ftype == 'raster':
                try:
                    os.remove(filename)
                except:
                    pass
            else:
                QgsVectorFileWriter.deleteShapeFile(filename)
        
    #-Function that applies a certain color scheme to a raster layer        
    def rasterSymbology(self, filename, layer):
        #-Define the lower, mid, and high value colors
        lcolor = [239, 90, 36] # sort of red
        mcolor = [255, 255, 153] #  sort of yellow
        hcolor = [0, 102, 204] # sort of blue
        #-Get layer properties
        provider = layer.dataProvider()
        extent = layer.extent()
        stats = provider.bandStatistics(1, QgsRasterBandStats.All,extent, 0)
        #-Calculate some raster statistics
        mean_value = stats.mean
        std2_value  = stats.stdDev * 2 # 2 x std
        minvalue = max(mean_value - std2_value, 0)
        maxvalue = max(mean_value + std2_value, 0)
        diff = maxvalue - minvalue
        classes = 5
        it_step = diff/(classes-1)
        #-Number of color classes to show
        classes = 5
        #-The middle class number
        mclass = math.ceil(classes/2.0)
        #-Iterative colorsteps between the first and middle color class, and middle and high color class, respectively
        cstep1 = []
        cstep2 = []
        for c in range(0, 3):
            cstep1.append((mcolor[c]-lcolor[c])/(mclass-1))
            cstep2.append((hcolor[c]-mcolor[c])/(mclass-1))
        lst = [] #-Empty list for adding the colors and values
        for c in range(1, classes+1):
            if c==1: #- first color class = minimum value
                color  = lcolor
                rastervalue = minvalue
            elif c<mclass:
                color = [lcolor[0] + (c-1) * cstep1[0], lcolor[1] + (c-1) * cstep1[1], lcolor[2] + (c-1) * cstep1[2]]
                rastervalue = minvalue + it_step * (c-1)
            elif c==mclass: #-middle color class
                color = mcolor
                rastervalue = minvalue + it_step * (c-1)
            elif c<classes:
                color = [mcolor[0] + (c-mclass) * cstep2[0], mcolor[1] + (c-mclass) * cstep2[1], mcolor[2] + (c-mclass) * cstep2[2]]
                rastervalue = minvalue + it_step * (c-1)
            elif c==classes:
                color = hcolor
                rastervalue = maxvalue
            lst.append(QgsColorRampShader.ColorRampItem(rastervalue, QtGui.QColor(color[0],color[1],color[2]),str(rastervalue)))

        myRasterShader = QgsRasterShader()
        myColorRamp = QgsColorRampShader()
        
        myColorRamp.setColorRampItemList(lst)
        myColorRamp.setColorRampType(QgsColorRampShader.INTERPOLATED)
        myRasterShader.setRasterShaderFunction(myColorRamp)
        
        myPseudoRenderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 
                                                            layer.type(),  
                                                            myRasterShader)
        
        layer.setRenderer(myPseudoRenderer)
        layer.triggerRepaint()
        iface.legendInterface().refreshLayerSymbology(layer)



    ################ NEW PROJECT, OPEN PROJECT, SAVE AND SAVE AS ############

    # function launched when new project button is clicked
    def createNewProject(self):
        # check for current project and ask to save
        if self.currentProject:
            mes = QtGui.QMessageBox()
            mes.setWindowTitle("Save current project")
            mes.setText("Do you want to save the current project?")
            mes.setStandardButtons(QtGui.QMessageBox.Save | QtGui.QMessageBox.No | QtGui.QMessageBox.Cancel)
            ret = mes.exec_()
            if ret == QtGui.QMessageBox.Save:
                self.saveProject()
                newproject = True
            elif ret == QtGui.QMessageBox.No: # create new project without saving current one
                newproject = True
            else:
                newproject = False
        else:
            newproject = True
        # check if a new project needs/can be created based on the criteria tested above    
        if newproject:
            self.currentConfig.read(os.path.join(os.path.dirname(__file__), "config", "preprocess_config_template.cfg"))
            # clear project canvas
            #qgsProject = QgsProject.instance()
            #qgsProject.clear()
            # save as new project
            self.saveAsProject("new")
            self.Tab.setCurrentIndex(0)
            
            
    # function launched when existing project is opened
    def openProject(self):
        # check for the current project and ask to save
        if self.currentProject:
            mes = QtGui.QMessageBox()
            mes.setWindowTitle("Save current project")
            mes.setText("Do you want to save the current project?")
            mes.setStandardButtons(QtGui.QMessageBox.Save | QtGui.QMessageBox.No | QtGui.QMessageBox.Cancel)
            ret = mes.exec_()
            if ret == QtGui.QMessageBox.Save:
                self.saveProject()
                openproject = True
            elif ret == QtGui.QMessageBox.No: # open project without saving current one
                openproject = True
            else:
                openproject = False
        else:
            openproject = True
        # check if a project can be opened based on the criteria tested above
        if openproject:
            tempname = QtGui.QFileDialog.getOpenFileName(self, "Open project *.cfg", self.projectDir,"*.cfg")
            if tempname:
                # set the new config file
                self.currentConfigFileName = tempname
                self.currentConfig.read(self.currentConfigFileName)
                
#                 # open the corresponding qgs file
#                 qgsProjectFileName = ((self.currentConfigFileName).split(".cfg")[0]) + ".qgs"
#                 qgsProject = QgsProject.instance()
#                 qgsProject.clear()
#                 qgsProject.setFileName(qgsProjectFileName)
#                 qgsProject.read()
                # save the project
                self.saveProject()
            
    # Save as project
    def saveAsProject(self, ptype=False):
        if ptype:
            tempname = QtGui.QFileDialog.getSaveFileName(self, 'Save '+ptype+' project as', self.projectDir, '*.cfg')
        else:
            tempname = QtGui.QFileDialog.getSaveFileName(self, 'Save current project as', self.projectDir, '*.cfg')
        if tempname:
            self.currentConfigFileName = tempname
            self.saveProject()
            
    # Save the project
    def saveProject(self):
        with open(self.currentConfigFileName, 'wb') as f:
            self.currentConfig.write(f)
        
#         if self.currentProject is False:
#             temp = self.currentConfigFileName
#             self.sphyLocationPath = temp.split(":")[0] + ":"

        
        self.settings.setValue("sphyPreProcessPlugin/currentConfig", self.currentConfigFileName)
        self.projectDir = os.path.dirname(self.currentConfigFileName)
        
#         # write the qgs project file
#         qgsProjectFileName = ((self.currentConfigFileName).split(".cfg")[0]) + ".qgs"
#         qgsProject = QgsProject.instance()
#         qgsProject.setFileName(qgsProjectFileName)
#         qgsProject.write()
#         self.settings.setValue("sphyplugin/qgsProjectFileName", qgsProjectFileName.replace("\\","/"))
        
        # update tab, project settings, and gui
        self.currentProject = True
        self.Tab.setEnabled(1)
        #self.exitDate = True
        self.initGuiConfigMap()
        #self.exitDate = False
        self.updateSaveButtons(0)
        
    # enable save buttons after gui has been modified
    def updateSaveButtons(self, flag):
        if self.currentProject:
            self.saveAsButton.setEnabled(1)
#             self.saveButton.setEnabled(flag) 
            


        
