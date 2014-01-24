#import filewatcher.modulebooster

# def profile_imports(threshold = 0.000005):
    # import time
    # _old_import = __import__
    # class depth:
        # depth = 0
        
    # def profiling_import(name, *args,**kwargs):
        # start_time = time.time()
        # depth.depth += 1
        # try:
            # result = _old_import(name, *args, **kwargs)
        # finally:
            # depth.depth -= 1
        # time_taken = time.time() - start_time
        # if time_taken > threshold:
            # print ' '*depth.depth + '[%.2f] import %s'%(time_taken, name)

        # return result
        
    # __builtins__.__dict__['__import__'] = profiling_import      

# profile_imports()
    
import cgi
import ctypes
import logging, logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time

# check if we should delay!
try:
    if '--delay' in sys.argv:
        delay = int(sys.argv[sys.argv.index('--delay')+1])
        time.sleep(delay)
except:
    print 'You should specify "--delay x" where x is an integer'

# Pythonlib imports
### Must be in this order
import zlock, h5_lock, h5py
zlock.set_client_process_name('BLACS')
###
from subproc_utils import zmq_get, ZMQServer
from setup_logging import setup_logging
import shared_drive

# Custom Excepthook
import excepthook
# Setup logging
logger = setup_logging()
excepthook.set_logger(logger)

# Import Qt
from PySide.QtCore import *
from PySide.QtGui import *
from PySide.QtUiTools import QUiLoader

# Connection Table Code
from connections import ConnectionTable
#Draggable Tab Widget Code
from qtutils.widgets.dragdroptab import DragDropTabWidget
# Lab config code
from LabConfig import LabConfig, config_prefix
# Qt utils for running functions in the main thread
from qtutils import *
# Analysis Submission code
from analysis_submission import AnalysisSubmission
# Queue Manager Code
from queue import QueueManager, QueueTreeview
# Hardware Interface Imports
import hardware_interfaces
for device in hardware_interfaces.device_list:    
    exec("from hardware_interfaces."+device+" import "+device)
# Save/restore frontpanel code
from front_panel_settings import FrontPanelSettings
# Notifications system
from notifications import Notifications
# Preferences system
from settings import Settings
#import settings_pages
import plugins

class BLACSWindow(QMainWindow):
       
    def closeEvent(self, event):
        #print 'aaaaa'
        if self.blacs.exit_complete:
            event.accept()
            if self.blacs._relaunch:
                logger.info('relaunching BLACS after quit')
                relaunch_delay = '2'
                if '--delay' in sys.argv:
                    index = sys.argv.index('--delay') + 1
                    try:
                        int(sys.argv[index])
                        sys.argv[index] = relaunch_delay
                    except:
                        sys.argv.insert(index,relaunch_delay)
                else:
                    sys.argv.append('--delay')
                    sys.argv.append(relaunch_delay)
                subprocess.Popen([sys.executable] + sys.argv)
        else:
            event.ignore()
            logger.info('destroy called')
            if not self.blacs.exiting:
                self.blacs.exiting = True
                self.blacs.queue.manager_running = False
                self.blacs.settings.close()
                for module_name, plugin in self.blacs.plugins.items():
                    try:
                        plugin.close()
                    except Exception as e:
                        logger.error('Could not close plugin %s. Error was: %s'%(module_name,str(e)))
                
                inmain_later(self.blacs.on_save_exit)
                
            QTimer.singleShot(100,self.close)
        
class BLACS(object):

    tab_widget_ids = 7
    
    def __init__(self,application):
        self.qt_application = application
        #self.qt_application.aboutToQuit.connect(self.destroy)
        self._relaunch = False
        self.exiting = False
        self.exit_complete = False
        
        #self.ui = BLACSWindow(self).ui
        loader = UiLoader()
        loader.registerCustomWidget(QueueTreeview)
        loader.registerCustomPromotion('BLACS',BLACSWindow)
        self.ui = loader.load('main.ui')
        self.ui.blacs=self
        self.tab_widgets = {}
        self.exp_config = exp_config # Global variable
        self.settings_path = settings_path # Global variable
        self.connection_table = connection_table # Global variable
        self.connection_table_h5file = self.exp_config.get('paths','connection_table_h5')
        self.connection_table_labscript = self.exp_config.get('paths','connection_table_py')
                
        # Setup the UI
        self.ui.main_splitter.setStretchFactor(0,0)
        self.ui.main_splitter.setStretchFactor(1,1)
        
        self.tablist = {}
        self.panes = {}
        self.settings_dict = {}
        # Instantiate Devices from Connection Table, Place in Array        
        self.attached_devices = self.connection_table.find_devices(hardware_interfaces.device_list)
        
        # Store the panes in a dictionary for easy access
        self.panes['tab_top_vertical_splitter'] = self.ui.tab_top_vertical_splitter
        self.panes['tab_bottom_vertical_splitter'] = self.ui.tab_bottom_vertical_splitter
        self.panes['tab_horizontal_splitter'] = self.ui.tab_horizontal_splitter
        self.panes['main_splitter'] = self.ui.main_splitter
                
        # Get settings to restore 
        self.front_panel_settings = FrontPanelSettings(self.settings_path, self.connection_table)
        self.front_panel_settings.setup(self)
        settings,question,error,tab_data = self.front_panel_settings.restore()
            
        # TODO: handle question/error cases
        
        self.restore_window(tab_data)
        
        #splash.update_text('Creating the device tabs...')
        # Create the notebooks
        for i in range(4):
            self.tab_widgets[i] = DragDropTabWidget(self.tab_widget_ids)
            getattr(self.ui,'tab_container_%d'%i).addWidget(self.tab_widgets[i])
        
        for device_name,device_class in self.attached_devices.items():
            self.settings_dict.setdefault(device_name,{"device_name":device_name})
            # add common keys to settings:
            self.settings_dict[device_name]["connection_table"] = self.connection_table
            self.settings_dict[device_name]["front_panel_settings"] = settings[device_name] if device_name in settings else {}
            self.settings_dict[device_name]["saved_data"] = tab_data[device_name]['data'] if device_name in tab_data else {}            
            # Instantiate the device            
            self.tablist[device_name] = globals()[device_class](self.tab_widgets[0],self.settings_dict[device_name])
        
        self.order_tabs(tab_data)
        
                         
        # setup analysis submission
        self.analysis_submission = AnalysisSubmission(self,self.ui)
        if 'analysis_data' not in tab_data['BLACS settings']:
            tab_data['BLACS settings']['analysis_data'] = {}
        else:
            tab_data['BLACS settings']['analysis_data'] = eval(tab_data['BLACS settings']['analysis_data'])
        self.analysis_submission.restore_save_data(tab_data['BLACS settings']["analysis_data"])
        # Setup the QueueManager
        self.queue = QueueManager(self,self.ui)  
        if 'queue_data' not in tab_data['BLACS settings']:
            tab_data['BLACS settings']['queue_data'] = {}
        else:
            tab_data['BLACS settings']['queue_data'] = eval(tab_data['BLACS settings']['queue_data'])
        self.queue.restore_save_data(tab_data['BLACS settings']['queue_data'])
        
        # setup the plugin system
        settings_pages = []
        self.plugins = {}
        plugin_settings = eval(tab_data['BLACS settings']['plugin_data']) if 'plugin_data' in tab_data['BLACS settings'] else {}
        for module_name, module in plugins.modules.items():
            try:
                # instantiate the plugin
                self.plugins[module_name] = module.Plugin(plugin_settings[module_name] if module_name in plugin_settings else {})     
            except Exception:
                logger.exception('Could not instantiate plugin \'%s\'. Skipping')
        
        blacs_data = {'exp_config':self.exp_config,
                      'ui':self.ui,
                      'set_relaunch':self.set_relaunch,
                      'plugins':self.plugins,
                      'connection_table_h5file':self.connection_table_h5file,
                      'connection_table_labscript':self.connection_table_labscript,
                     }
        
        def create_menu(parent, menu_parameters):
            if 'name' in menu_parameters:
                if 'menu_items' in menu_parameters:
                    child = parent.addMenu(menu_parameters['name'])
                    for child_menu_params in menu_parameters['menu_items']:
                        create_menu(child,child_menu_params)
                else:
                    child = parent.addAction(menu_parameters['name'])
                    
                if 'action' in menu_parameters:                    
                    child.triggered.connect(menu_parameters['action'])                    
                    
            elif 'separator' in menu_parameters:
                parent.addSeparator()
        
        # setup the Notification system
        self.notifications = Notifications(blacs_data)
        
        settings_callbacks = []
        for module_name, plugin in self.plugins.items():
            try:
                # Setup settings page
                settings_pages.extend(plugin.get_setting_classes())
                # Setup menu
                if plugin.get_menu_class():
                    # must store a reference or else the methods called when the menu actions are triggered
                    # (contained in this object) will be garbaged collected
                    menu = plugin.get_menu_class()(blacs_data)
                    create_menu(self.ui.menubar,menu.get_menu_items())
                    plugin.set_menu_instance(menu)
                        
                # Setup notifications
                plugin_notifications = {}
                for notification_class in plugin.get_notification_classes():
                    self.notifications.add_notification(notification_class)
                    plugin_notifications[notification_class] = self.notifications.get_instance(notification_class)
                plugin.set_notification_instances(plugin_notifications)
                
                # Register callbacks
                callbacks = plugin.get_callbacks()
                # save the settings_changed callback in a separate list for setting up later
                if isinstance(callbacks,dict) and 'settings_changed' in callbacks:
                    settings_callbacks.append(callbacks['settings_changed'])
                
            except Exception:
                logger.exception('Plugin \'%s\' error. Plugin may not be functional.'%module_name)
                
                
        # setup the BLACS preferences system
        self.settings = Settings(file=self.settings_path, parent = self.ui, page_classes=settings_pages)
        for callback in settings_callbacks:            
            self.settings.register_callback(callback)
        
        # update the blacs_data dictionary with the settings system
        blacs_data['settings'] = self.settings
            
        for module_name, plugin in self.plugins.items():
            try:
                plugin.plugin_setup_complete()
            except Exception:
                logger.exception('Plugin \'%s\' error. Plugin may not be functional.'%module_name)
        
        # Connect menu actions
        self.ui.actionOpenPreferences.triggered.connect(self.on_open_preferences)
        self.ui.actionSave.triggered.connect(self.on_save_front_panel)
        self.ui.actionOpen.triggered.connect(self.on_load_front_panel)       
        
        self.ui.show()
    
    def set_relaunch(self,value):
        self._relaunch = bool(value)
    
    def restore_window(self,tab_data):
        # read out position settings:
        try:
            # There are some dodgy hacks going on here to try and restore the window position correctly
            # Unfortunately Qt has two ways of measuring teh window position, one with the frame/titlebar
            # and one without. If you use the one that measures including the titlebar, you don't
            # know what the window size was when the window was UNmaximized.
            #
            # Anyway, no idea if this works cross platform (tested on windows 8)
            # Feel free to rewrite this, along with the code in front_panel_settings.py
            # which stores the values
            #
            # Actually this is a waste of time because if you close when maximized, reoopen and then 
            # de-maximize, the window moves to a random position (not the position it was at before maximizing)
            # so bleh!
            self.ui.move(tab_data['BLACS settings']["window_xpos"]-tab_data['BLACS settings']['window_frame_width']/2,tab_data['BLACS settings']["window_ypos"]-tab_data['BLACS settings']['window_frame_height']+tab_data['BLACS settings']['window_frame_width']/2)
            self.ui.resize(tab_data['BLACS settings']["window_width"],tab_data['BLACS settings']["window_height"])
            
            if 'window_maximized' in tab_data['BLACS settings'] and tab_data['BLACS settings']['window_maximized']:
                self.ui.showMaximized()
            
            for pane_name,pane in self.panes.items():
                pane.setSizes(tab_data['BLACS settings'][pane_name])
                    
        except Exception as e:
            logger.warning("Unable to load window and notebook defaults. Exception:"+str(e))
    
    def order_tabs(self,tab_data):
        # Move the tabs to the correct notebook
        for device_name,device_class in self.attached_devices.items():
            notebook_num = 0
            if device_name in tab_data:
                notebook_num = int(tab_data[device_name]["notebook"])
                if notebook_num not in self.tab_widgets: 
                    notebook_num = 0
                    
            #Find the notebook the tab is in, and remove it:
            for notebook in self.tab_widgets.values():
                tab_index = notebook.indexOf(self.tablist[device_name]._ui)
                if tab_index != -1:
                    notebook.removeTab(tab_index)
                    self.tab_widgets[notebook_num].addTab(self.tablist[device_name]._ui,device_name)
                    break
        
        # splash.update_text('restoring tab positions...')
        # # Now that all the pages are created, reorder them!
        for device_name,device_class in self.attached_devices.items():
            if device_name in tab_data:
                notebook_num = int(tab_data[device_name]["notebook"])
                if notebook_num in self.tab_widgets:  
                    self.tab_widgets[notebook_num].tab_bar.moveTab(self.tab_widgets[notebook_num].indexOf(self.tablist[device_name]._ui),int(tab_data[device_name]["page"]))
        
        # # Now that they are in the correct order, set the correct one visible
        for device_name,device_data in tab_data.items():
            if device_name == 'BLACS settings':
                continue
            # if the notebook still exists and we are on the entry that is visible
            if bool(device_data["visible"]) and int(device_data["notebook"]) in self.tab_widgets:
                self.tab_widgets[int(device_data["notebook"])].tab_bar.setCurrentIndex(int(device_data["page"]))
    
    def update_all_tab_settings(self,settings,tab_data):
        for device_name,tab in self.tablist.items():
            self.settings_dict[device_name]["front_panel_settings"] = settings[device_name] if device_name in settings else {}
            self.settings_dict[device_name]["saved_data"] = tab_data[device_name]['data'] if device_name in tab_data else {}            
            tab.update_from_settings(self.settings_dict[device_name])
                    
        
    def on_load_front_panel(self,*args,**kwargs):
        # get the file:
        # create file chooser dialog
        dialog = QFileDialog(None,"Select file to load", self.exp_config.get('paths','experiment_shot_storage'), "HDF5 files (*.h5 *.hdf5)")
        dialog.setViewMode(QFileDialog.Detail)
        dialog.setFileMode(QFileDialog.ExistingFile)
        if dialog.exec_():
            selected_files = dialog.selectedFiles()
            filepath = str(selected_files[0])
            # Qt has this weird behaviour where if you type in the name of a file that exists
            # but does not have the extension you have limited the dialog to, the OK button is greyed out
            # but you can hit enter and the file will be selected. 
            # So we must check the extension of each file here!
            if filepath.endswith('.h5') or filepath.endswith('.hdf5'):
                try:
                    # TODO: Warn that this will restore values, but not channels that are locked
                    message = QMessageBox()
                    message.setText("""Warning: This will modify front panel values and cause device output values to update.
                    \nThe queue and files waiting to be sent for analysis will be cleared.
                    \n
                    \nNote: Channels that are locked will not be updated.\n\nDo you wish to continue?""")
                    message.setIcon(QMessageBox.Warning)
                    message.setWindowTitle("BLACS")
                    message.setStandardButtons(QMessageBox.Yes|QMessageBox.No)
                   
                    if message.exec_() == QMessageBox.Yes:                
                        front_panel_settings = FrontPanelSettings(filepath, self.connection_table)
                        settings,question,error,tab_data = front_panel_settings.restore()
                        #TODO: handle question/error
                        
                        # Restore window data
                        self.restore_window(tab_data)
                        self.order_tabs(tab_data)                   
                        self.update_all_tab_settings(settings,tab_data)
                        
                        # restore queue data
                        if 'queue_data' not in tab_data['BLACS settings']:
                            tab_data['BLACS settings']['queue_data'] = {}
                        else:
                            tab_data['BLACS settings']['queue_data'] = eval(tab_data['BLACS settings']['queue_data'])
                        self.queue.restore_save_data(tab_data['BLACS settings']['queue_data'])
                        # restore analysis data
                        if 'analysis_data' not in tab_data['BLACS settings']:
                            tab_data['BLACS settings']['analysis_data'] = {}
                        else:
                            tab_data['BLACS settings']['analysis_data'] = eval(tab_data['BLACS settings']['analysis_data'])
                        self.analysis_submission.restore_save_data(tab_data['BLACS settings']["analysis_data"])
                except Exception as e:
                    logger.exception("Unable to load the front panel in %s."%(filepath))
                    message = QMessageBox()
                    message.setText("Unable to load the front panel. The error encountered is printed below.\n\n%s"%str(e))
                    message.setIcon(QMessageBox.Information)
                    message.setWindowTitle("BLACS")
                    message.exec_() 
                finally:
                    dialog.deleteLater()
            else:
                dialog.deleteLater()
                message = QMessageBox()
                message.setText("You did not select a file ending with .h5 or .hdf5. Please try again")
                message.setIcon(QMessageBox.Information)
                message.setWindowTitle("BLACS")
                message.exec_()
                QTimer.singleShot(10,self.on_load_front_panel)
    
    def on_save_exit(self):
        # Save front panel
        data = self.front_panel_settings.get_save_data()
       
        with h5py.File(self.settings_path,'r+') as h5file:
           if 'connection table' in h5file:
               del h5file['connection table']
        
        self.front_panel_settings.save_front_panel_to_h5(self.settings_path,data[0],data[1],data[2],data[3],{"overwrite":True})
        logger.info('Destroying tabs')
        for tab in self.tablist.values():
            tab.destroy()            
            
        #gobject.timeout_add(100,self.finalise_quit,time.time())
        QTimer.singleShot(100,lambda: self.finalise_quit(time.time()))
    
    def finalise_quit(self,initial_time):
        logger.info('finalise_quit called')
        tab_close_timeout = 2
        # Kill any tabs which didn't close themselves:
        for name, tab in self.tablist.items():
            if tab.destroy_complete:
                del self.tablist[name]
        if self.tablist:
            for name, tab in self.tablist.items():
                # If a tab has a fatal error or is taking too long to close, force close it:
                if (time.time() - initial_time > tab_close_timeout) or tab.state == 'fatal error':
                    try:
                        tab.close_tab() 
                    except Exception as e:
                        logger.error('Couldn\'t close tab:\n%s'%str(e))
                    del self.tablist[name]
        if self.tablist:
            QTimer.singleShot(100,lambda: self.finalise_quit(initial_time))
        else:
            self.exit_complete = True
            logger.info('quitting')
    
    def on_save_front_panel(self,*args,**kwargs):
        data = self.front_panel_settings.get_save_data()
    
        # Open save As dialog
        dialog = QFileDialog(None,"Save BLACS state", self.exp_config.get('paths','experiment_shot_storage'), "HDF5 files (*.h5)")
        try:
            dialog.setViewMode(QFileDialog.Detail)
            dialog.setFileMode(QFileDialog.AnyFile)
            dialog.setAcceptMode(QFileDialog.AcceptSave)
            
            if dialog.exec_():
                current_file = str(dialog.selectedFiles()[0])
                if not current_file.endswith('.h5'):
                    current_file += '.h5'
                self.front_panel_settings.save_front_panel_to_h5(current_file,data[0],data[1],data[2],data[3])
        except Exception:
            raise
        finally:
            dialog.deleteLater()
        
    def on_open_preferences(self,*args,**kwargs):
        self.settings.create_dialog()
                
class ExperimentServer(ZMQServer):
    def handler(self, h5_filepath):
        print h5_filepath
        message = self.process(h5_filepath)
        logger.info('Request handler: %s ' % message.strip())
        return message

    @inmain_decorator(wait_for_return=True)
    def process(self,h5_filepath):
        # Convert path to local slashes and shared drive prefix:
        logger.info('received filepath: %s'%h5_filepath)        
        h5_filepath = shared_drive.path_to_local(h5_filepath)
        logger.info('local filepath: %s'%h5_filepath)
        return app.queue.process_request(h5_filepath)

 
if __name__ == '__main__':
    if 'tracelog' in sys.argv:
        ##########
        import tracelog
        tracelog.log('blacs_trace.log',['__main__','BLACS.tab_base_classes',
                                        'qtutils',
                                        'qtutils.widgets.ddsoutput',
                                        'qtutils.widgets.analogoutput',
                                        'BLACS.hardware_interfaces.ni_pcie_6363',
                                        'BLACS.hardware_interfaces.output_classes',
                                        'BLACS.device_base_class',
                                        'BLACS.tab_base_classes',
                                        'BLACS.plugins.connection_table',
                                        'BLACS.recompile_and_restart',
                                        'filewatcher',
                                        'queue',
                                        'notifications',
                                        'analysis_submission',
                                        'settings'
                                       ], sub=True)
        ##########


    config_path = os.path.join(config_prefix,'%s.ini'%socket.gethostname())
    settings_path = os.path.join(config_prefix,'%s_BLACS.h5'%socket.gethostname())
    required_config_params = {"DEFAULT":["experiment_name"],
                              "programs":["text_editor",
                                          "text_editor_arguments",
                                         ],
                              "paths":["shared_drive",
                                       "connection_table_h5",
                                       "connection_table_py",                                       
                                      ],
                              "ports":["BLACS", "lyse"],
                             }
    exp_config = LabConfig(config_path,required_config_params)        
    
    port = int(exp_config.get('ports','BLACS'))
    myappid = 'monashbec.BLACS' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass
    # Start experiment server
    experiment_server = ExperimentServer(port)

    # Create Connection Table object
    try:
        connection_table = ConnectionTable(exp_config.get('paths','connection_table_h5'))
    except:
        # dialog = gtk.MessageDialog(None,gtk.DIALOG_MODAL,gtk.MESSAGE_ERROR,gtk.BUTTONS_NONE,"The connection table in '%s' is not valid. Please check the compilation of the connection table for errors\n\n"%self.connection_table_h5file)
             
        # dialog.run()
        # dialog.destroy()
        sys.exit("Invalid Connection Table")
        
    
    qapplication = QApplication(sys.argv)
    app = BLACS(qapplication)
    
    def execute_program():
        qapplication.exec_()        
        experiment_server.shutdown()
    
    sys.exit(execute_program())
