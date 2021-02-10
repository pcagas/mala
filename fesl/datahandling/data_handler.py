from torch.utils.data import TensorDataset

from .data_scaler import DataScaler
from .snapshot import Snapshot
from .lazy_load_dataset import LazyLoadDataset
from fesl.common.parameters import Parameters
from fesl.targets.target_interface import TargetInterface
from fesl.descriptors.descriptor_interface import DescriptorInterface
from fesl.common.printout import printout
import numpy as np
import torch

class DataHandler:
    """
    Handles data. Can only process numpy arrays at the moment. Data that is not in a numpy array can be converted using
    the DataConverter class.
    """

    def __init__(self, p: Parameters, target_calculator=None, descriptor_calculator=None, input_data_scaler=None, output_data_scaler=None):
        """

        Parameters
        ----------
        p : fesl.common.parameters.Parameters
        descriptor_calculator : fesl.descriptors.descriptor_base.DescriptorBase or derivative
            Used to do unit conversion on input data. If None, then one will be created by this class.
        target_calculator : fesl.targets.target_base.TargetBase or derivative
            Used to do unit conversion on output data. If None, then one will be created by this class.
        input_data_scaler : fesl.datahandling.data_scaler.DataScaler
            Used to scale the input data. If None, then one will be created by this class.
        output_data_scaler : fesl.datahandling.data_scaler.DataScaler
            Used to scale the output data. If None, then one will be created by this class.
        """
        self.parameters = p.data
        self.dbg_grid_dimensions = p.debug.grid_dimensions
        self.use_horovod = p.use_horovod
        self.training_data_set = None

        self.validation_data_set = None

        self.test_data_set = None

        self.input_data_scaler = input_data_scaler
        if self.input_data_scaler is None:
            self.input_data_scaler = DataScaler(self.parameters.input_rescaling_type)

        self.output_data_scaler = output_data_scaler
        if self.output_data_scaler is None:
            self.output_data_scaler = DataScaler(self.parameters.output_rescaling_type)

        self.target_calculator = target_calculator
        if self.target_calculator is None:
            self.target_calculator = TargetInterface(p)

        self.descriptor_calculator = descriptor_calculator
        if self.descriptor_calculator is None:
            self.descriptor_calculator = DescriptorInterface(p)

        self.nr_snapshots = 0
        self.grid_dimension = [0,0,0]
        self.grid_size = 0
        self.nr_training_data = 0
        self.nr_test_data = 0
        self.nr_validation_data = 0

        self.training_data_inputs = torch.empty(0)
        """
        Torch tensor holding all scaled training data inputs.
        """

        self.validation_data_inputs = torch.empty(0)
        """
        Torch tensor holding all scaled validation data inputs.
        """

        self.test_data_inputs = torch.empty(0)
        """
        Torch tensor holding all scaled testing data inputs.
        """

        self.training_data_outputs = torch.empty(0)
        """
        Torch tensor holding all scaled training data output.
        """

        self.validation_data_outputs = torch.empty(0)
        """
        Torch tensor holding all scaled validation data output.
        """

        self.test_data_outputs = torch.empty(0)
        """
        Torch tensor holding all scaled testing data output.
        """


    def add_snapshot(self, input_npy_file, input_npy_directory, output_npy_file, output_npy_directory,
                     input_units="None", output_units="1/eV"):
        """
        Adds a snapshot to data handler.

        Parameters
        ----------
        input_npy_file : string
            File with saved numpy input array.
        input_npy_directory : string
            Directory containing input_npy_directory.
        output_npy_file : string
            File with saved numpy output array.
        output_npy_directory : string
            Directory containing output_npy_file.
        input_units : string
            Units of input data. See descriptor classes to see which units are supported.
        output_units : string
            Units of output data. See target classes to see which units are supported.
        Returns
        -------

        """
        snapshot = Snapshot(input_npy_file, input_npy_directory, input_units, output_npy_file, output_npy_directory,
                                       output_units)
        self.parameters.snapshot_directories_list.append(snapshot)


    def clear_data(self):
        """
        Resets the entire data handling. Useful when doing multiple investigations in the same python file.

        Returns
        -------
        """
        self.training_data_set = None
        self.validation_data_set = None
        self.test_data_set = None

        self.parameters.snapshot_directories_list = []

    def prepare_data(self, reparametrize_scaler=True):
        """
        Prepares the data to be used in a training process,.
        This includes:
            - Checking snapshots for consistency
            - Parametrizing the DataScalers (if desired)
            - Building DataSet objects.
        Parameters
        ----------
        reparametrize_scaler : bool
            If True (default), the DataScalers are parametrized based on the training data.

        Returns
        -------

        """
        # Do a consistency check of the snapshots so that we don't run into an error later.
        # If there is an error, check_snapshots() will raise an exception.
        printout("Checking the snapshots and your inputs for consistency.")
        self.__check_snapshots()
        printout("Consistency check successful.")

        # Parametrize the scalers, if needed.
        printout("Initializing the data scalers.")
        if reparametrize_scaler:
            self.__parametrize_scalers()
        printout("Data scalers initialized.")


        # Build Datasets.
        printout("Build datasets.")
        self.__build_datasets()
        printout("Build dataset done.")

    def mix_datasets(self):
        """
        Mixes the ordering with which the snapshots are read (in the lazy-loading case).
        Returns
        -------

        """
        if self.parameters.use_lazy_loading:
            self.validation_data_set.mix_datasets()
            self.test_data_set.mix_datasets()
            self.training_data_set.mix_datasets()


    def raw_numpy_to_converted_scaled_tensor(self, numpy_array, data_type, units, convert3Dto1D=False):
        """
        Transforms a raw numpy array containing inputs or outputs into a scaled torch tensor with the right units,
        i.e. a tensor that can simply be put into a FESL network.
        Parameters
        ----------
        numpy_array : np.array
            Array that is to be converted.
        data_type : string
            Either "in" or "out", depending if input or output data is processed.
        units : string
            Units of the data that is processed.
        convert3Dto1D : bool
            If True (default: False), then a (x,y,z,dim) array is transformed into a (x*y*z,dim) array.
        Returns
        -------
        converted_tensor: torch.Tensor
            The fully converted and scaled tensor.
        """
        # Check parameters for consistency.
        if data_type != "in" and data_type != "out":
            raise Exception("Please specify either \"in\" or \"out\" as data_type.")

        # Convert units of numpy array.
        numpy_array = self.__raw_numpy_to_converted_numpy(numpy_array, data_type, units)

        # If desired, the dimensions can be changed.
        if convert3Dto1D:
            if data_type == "in":
                data_dimension = self.get_input_dimension()
            else:
                data_dimension = self.get_output_dimension()
            desired_dimensions = [self.grid_size, data_dimension]
        else:
            desired_dimensions = None

        # Convert numpy array to scaled tensor a network can work with.
        numpy_array = self.__converted_numpy_to_scaled_tensor(numpy_array, desired_dimensions, data_type)
        return numpy_array


    def __check_snapshots(self):
        """
        Checks the snapshots for consistency. If inconsistencies are found, an exception is thrown.
        Returns
        -------
        """
        self.nr_snapshots = len(self.parameters.snapshot_directories_list)

        # Read the snapshots using a memorymap to see if there is consistency.
        firstsnapshot = True
        for snapshot in self.parameters.snapshot_directories_list:
            ####################
            # Descriptors.
            ####################

            printout("Checking descriptor file ", snapshot.input_npy_file, "at", snapshot.input_npy_directory)
            tmp = self.__load_from_npy_file(snapshot.input_npy_directory + snapshot.input_npy_file,
                                            mmapmode='r')

            # We have to cut xyz information, if we have xyz information in the descriptors.
            if self.parameters.descriptors_contain_xyz:
                # Remove first 3 elements of descriptors, as they correspond
                # to the x,y and z information.
                tmp = tmp[:, :, :, 3:]

            # The first snapshot determines the data size to be used.
            # We need to make sure that snapshot size is consistent.
            tmp_input_dimension = np.shape(tmp)[-1]
            tmp_grid_dim = np.shape(tmp)[0:3]
            if firstsnapshot:
                self.input_dimension = tmp_input_dimension
                self.grid_dimension[0:3] = tmp_grid_dim[0:3]
            else:
                if (self.input_dimension != tmp_input_dimension
                        or self.grid_dimension[0] != tmp_grid_dim[0]
                        or self.grid_dimension[1] != tmp_grid_dim[1]
                        or self.grid_dimension[2] != tmp_grid_dim[2]):
                    raise Exception("Invalid snapshot entered at ", snapshot.input_npy_file)


            ####################
            # Targets.
            ####################

            printout("Checking targets file ", snapshot.output_npy_file, "at", snapshot.output_npy_directory)
            tmp_out = self.__load_from_npy_file(snapshot.output_npy_directory + snapshot.output_npy_file,
                                                mmapmode='r')

            # The first snapshot determines the data size to be used.
            # We need to make sure that snapshot size is consistent.
            tmp_output_dimension = np.shape(tmp_out)[-1]
            tmp_grid_dim = np.shape(tmp_out)[0:3]
            if firstsnapshot:
                self.output_dimension = tmp_output_dimension
            else:
                if self.output_dimension != tmp_output_dimension:
                    raise Exception("Invalid snapshot entered at ", snapshot.output_npy_file)
            if (self.grid_dimension[0] != tmp_grid_dim[0]
                    or self.grid_dimension[1] != tmp_grid_dim[1]
                    or self.grid_dimension[2] != tmp_grid_dim[2]):
                raise Exception("Invalid snapshot entered at ", snapshot.output_npy_file)

            if firstsnapshot:
                firstsnapshot = False

        # Save the grid size.
        self.grid_size = self.grid_dimension[0] * self.grid_dimension[1] * self.grid_dimension[2]

        # Now we need to confirm that the snapshot list has some inner consistency.
        if self.parameters.data_splitting_type == "by_snapshot":
            for snapshot_function in self.parameters.data_splitting_snapshots:
                if snapshot_function == "tr":
                    self.nr_training_data += 1
                elif snapshot_function == "te":
                    self.nr_test_data += 1
                elif snapshot_function == "va":
                    self.nr_validation_data += 1
                else:
                    raise Exception("Unknown option for snapshot splitting selected.")

            # Now we need to check whether or not this input is believable.
            nr_of_snapshots = len(self.parameters.snapshot_directories_list)
            if nr_of_snapshots != (self.nr_training_data + self.nr_validation_data + self.nr_test_data):
                raise Exception("Cannot split snapshots with specified splitting scheme, "
                                "too few or too many options selected")
            if self.nr_training_data == 0:
                raise Exception("No training snapshots provided.")
            if self.nr_validation_data == 0:
                raise Exception("No validation snapshots provided.")
            if self.nr_test_data == 0:
                raise Exception("No testing snapshots provided.")

        else:
            raise Exception("Wrong parameter for data splitting provided.")

        # As we are not actually interested in the number of snapshots, but in the number of datasets,
        # we need to multiply by that.
        self.nr_training_data *= self.grid_size
        self.nr_validation_data *= self.grid_size
        self.nr_test_data *= self.grid_size



    def __load_from_npy_file(self, file, mmapmode=None):
        """
        Loads a numpy array from a file.
        Parameters
        ----------
        file : string
            File from which the numpy array is loaded.
        mmapmode : string
            memory map mode that is used for loading; see numpy documentation for more details.

        Returns
        -------
        loaded_array : numpy.array
            The loaded array.
        """
        loaded_array = np.load(file, mmap_mode=mmapmode)
        if len(self.dbg_grid_dimensions) == 3:
            try:
                return loaded_array[0:self.dbg_grid_dimensions[0], 0:self.dbg_grid_dimensions[1], 0:self.dbg_grid_dimensions[2], :]
            except:
                printout(
                    "Could not use grid reduction, falling back to regular grid. Please check that the debug grid is "
                    "not bigger than the actual grid.")

        else:
            return loaded_array


    def __parametrize_scalers(self):
        """
        Uses the training data to parametrize the DataScalers.
        Returns
        -------
        """

        ##################
        # Inputs.
        ##################

        # If we do lazy loading, we have to iterate over the files one at a time and add them to the fit,
        # i.e. incrementally updating max/min or mean/std.
        # If we DON'T do lazy loading, we can simply load the training data (we will need it later anyway)
        # and perform the scaling. This should save some performance.

        if self.parameters.use_lazy_loading:
            i = 0
            self.input_data_scaler.start_incremental_fitting()
            # We need to perform the data scaling over the entirety of the training data.
            for snapshot in self.parameters.snapshot_directories_list:
                # Data scaling is only performed on the training data sets.
                if self.parameters.data_splitting_snapshots[i] == "tr":
                    tmp = self.__load_from_npy_file(snapshot.input_npy_directory + snapshot.input_npy_file,
                                                    mmapmode='r')
                    if self.parameters.descriptors_contain_xyz:
                        tmp = tmp[:, :, :, 3:]

                    # The scalers will later operate on torch Tensors so we have to make sure they are fitted on
                    # torch Tensors as well. Preprocessing the numpy data as follows does NOT load it into memory, see
                    # test/tensor_memory.py
                    tmp = np.array(tmp)
                    tmp *= self.descriptor_calculator.convert_units(1, snapshot.input_units)
                    tmp = tmp.astype(np.float32)
                    tmp = tmp.reshape([self.grid_size, self.get_input_dimension()])
                    tmp = torch.from_numpy(tmp).float()
                    self.input_data_scaler.incremental_fit(tmp)
                i += 1
            self.input_data_scaler.finish_incremental_fitting()

        else:
            self.training_data_inputs = []
            i = 0
            # We need to perform the data scaling over the entirety of the training data.
            for snapshot in self.parameters.snapshot_directories_list:

                # Data scaling is only performed on the training data sets.
                if self.parameters.data_splitting_snapshots[i] == "tr":
                    tmp = self.__load_from_npy_file(snapshot.input_npy_directory + snapshot.input_npy_file,
                                                    mmapmode='r')
                    if self.parameters.descriptors_contain_xyz:
                        tmp = tmp[:, :, :, 3:]
                    tmp = np.array(tmp)
                    tmp *= self.descriptor_calculator.convert_units(1, snapshot.input_units)
                    self.training_data_inputs.append(tmp)
                i += 1

            # The scalers will later operate on torch Tensors so we have to make sure they are fitted on
            # torch Tensors as well. Preprocessing the numpy data as follows does NOT load it into memory, see
            # test/tensor_memory.py
            self.training_data_inputs = np.array(self.training_data_inputs)
            self.training_data_inputs = self.training_data_inputs.astype(np.float32)
            self.training_data_inputs = self.training_data_inputs.reshape([self.nr_training_data, self.get_input_dimension()])
            self.training_data_inputs = torch.from_numpy(self.training_data_inputs).float()
            self.input_data_scaler.fit(self.training_data_inputs)
            self.training_data_inputs = self.input_data_scaler.transform(self.training_data_inputs)

        printout("Input scaler parametrized.")
        # ##################
        # # Outputs.
        # ##################

        # If we do lazy loading, we have to iterate over the files one at a time and add them to the fit,
        # i.e. incrementally updating max/min or mean/std.
        # If we DON'T do lazy loading, we can simply load the training data (we will need it later anyway)
        # and perform the scaling. This should save some performance.

        ##################
        # Inputs.
        ##################

        # If we do lazy loading, we have to iterate over the files one at a time and add them to the fit,
        # i.e. incrementally updating max/min or mean/std.
        # If we DON'T do lazy loading, we can simply load the training data (we will need it later anyway)
        # and perform the scaling. This should save some performance.

        if self.parameters.use_lazy_loading:
            i = 0
            self.output_data_scaler.start_incremental_fitting()
            # We need to perform the data scaling over the entirety of the training data.
            for snapshot in self.parameters.snapshot_directories_list:
                # Data scaling is only performed on the training data sets.
                if self.parameters.data_splitting_snapshots[i] == "tr":
                    tmp = self.__load_from_npy_file(snapshot.output_npy_directory + snapshot.output_npy_file,
                                                    mmapmode='r')
                    # The scalers will later operate on torch Tensors so we have to make sure they are fitted on
                    # torch Tensors as well. Preprocessing the numpy data as follows does NOT load it into memory, see
                    # test/tensor_memory.py
                    tmp = np.array(tmp)
                    tmp *= self.target_calculator.convert_units(1, snapshot.output_units)
                    tmp = tmp.astype(np.float32)
                    tmp = tmp.reshape([self.grid_size, self.get_output_dimension()])
                    tmp = torch.from_numpy(tmp).float()
                    self.output_data_scaler.incremental_fit(tmp)
                i += 1
            self.output_data_scaler.finish_incremental_fitting()

        else:
            self.training_data_outputs = []
            i = 0
            # We need to perform the data scaling over the entirety of the training data.
            for snapshot in self.parameters.snapshot_directories_list:

                # Data scaling is only performed on the training data sets.
                if self.parameters.data_splitting_snapshots[i] == "tr":
                    tmp = self.__load_from_npy_file(snapshot.output_npy_directory + snapshot.output_npy_file,
                                                    mmapmode='r')
                    tmp = np.array(tmp)
                    tmp *= self.target_calculator.convert_units(1, snapshot.output_units)
                    self.training_data_outputs.append(tmp)
                i += 1

            # The scalers will later operate on torch Tensors so we have to make sure they are fitted on
            # torch Tensors as well. Preprocessing the numpy data as follows does NOT load it into memory, see
            # test/tensor_memory.py
            self.training_data_outputs = np.array(self.training_data_outputs)
            self.training_data_outputs = self.training_data_outputs.astype(np.float32)
            self.training_data_outputs = self.training_data_outputs.reshape([self.nr_training_data, self.get_output_dimension()])
            self.training_data_outputs = torch.from_numpy(self.training_data_outputs).float()
            self.output_data_scaler.fit(self.training_data_outputs)
            self.training_data_outputs = self.output_data_scaler.transform(self.training_data_outputs)

        printout("Output scaler parametrized.")


    def __build_datasets(self):
        """
        Builds the DataSets that are used during training.
        Returns
        -------
        """
        if self.parameters.use_lazy_loading:

            # Create the lazy loading data sets.
            self.training_data_set = LazyLoadDataset(self.get_input_dimension(), self.get_output_dimension(), self.input_data_scaler, self.output_data_scaler,
                                                     self.descriptor_calculator, self.target_calculator,
                                                     self.grid_dimension, self.grid_size, self.parameters.descriptors_contain_xyz, self.use_horovod)
            self.validation_data_set = LazyLoadDataset(self.get_input_dimension(), self.get_output_dimension(), self.input_data_scaler, self.output_data_scaler,
                                                       self.descriptor_calculator, self.target_calculator,
                                                       self.grid_dimension, self.grid_size, self.parameters.descriptors_contain_xyz, self.use_horovod)
            self.test_data_set = LazyLoadDataset(self.get_input_dimension(), self.get_output_dimension(), self.input_data_scaler, self.output_data_scaler,
                                                 self.descriptor_calculator, self.target_calculator,
                                                 self.grid_dimension, self.grid_size, self.parameters.descriptors_contain_xyz, self.use_horovod)

            # Add snapshots to the lazy loading data sets.
            i = 0
            for snapshot in self.parameters.snapshot_directories_list:
                if self.parameters.data_splitting_snapshots[i] =="tr":
                    self.training_data_set.add_snapshot_to_dataset(snapshot)
                if self.parameters.data_splitting_snapshots[i] =="va":
                    self.validation_data_set.add_snapshot_to_dataset(snapshot)
                if self.parameters.data_splitting_snapshots[i] =="te":
                    self.test_data_set.add_snapshot_to_dataset(snapshot)
                i += 1

            self.training_data_set.mix_datasets()
            self.validation_data_set.mix_datasets()
            self.test_data_set.mix_datasets()
        else:
            # We iterate through the snapshots and add the validation data and test data.
            self.test_data_inputs = []
            self.validation_data_inputs = []
            self.test_data_outputs = []
            self.validation_data_outputs = []
            i = 0
            # We need to perform the data scaling over the entirety of the training data.
            for snapshot in self.parameters.snapshot_directories_list:

                # Data scaling is only performed on the training data sets.
                if self.parameters.data_splitting_snapshots[i] == "va" or self.parameters.data_splitting_snapshots[i] == "te":
                    tmp = self.__load_from_npy_file(snapshot.input_npy_directory + snapshot.input_npy_file,
                                                    mmapmode='r')
                    if self.parameters.descriptors_contain_xyz:
                        tmp = tmp[:, :, :, 3:]
                    tmp = np.array(tmp)
                    tmp *= self.descriptor_calculator.convert_units(1, snapshot.input_units)
                    if self.parameters.data_splitting_snapshots[i] == "va":
                        self.validation_data_inputs.append(tmp)
                    if self.parameters.data_splitting_snapshots[i] == "te":
                        self.test_data_inputs.append(tmp)
                    tmp = self.__load_from_npy_file(snapshot.output_npy_directory + snapshot.output_npy_file,
                                                    mmapmode='r')
                    tmp = np.array(tmp)
                    tmp *= self.target_calculator.convert_units(1, snapshot.output_units)
                    if self.parameters.data_splitting_snapshots[i] == "va":
                        self.validation_data_outputs.append(tmp)
                    if self.parameters.data_splitting_snapshots[i] == "te":
                        self.test_data_outputs.append(tmp)

                i += 1

            # I know this would be more elegant with the member functions typed below. But I am pretty sure
            # that that would create a temporary copy of the arrays, and that could overload the RAM
            # in cases where lazy loading is technically not even needed.
            self.test_data_inputs = np.array(self.test_data_inputs)
            self.test_data_inputs = self.test_data_inputs.astype(np.float32)
            self.test_data_inputs = self.test_data_inputs.reshape([self.nr_test_data, self.get_input_dimension()])
            self.test_data_inputs = torch.from_numpy(self.test_data_inputs).float()
            self.test_data_inputs = self.input_data_scaler.transform(self.test_data_inputs)

            self.validation_data_inputs = np.array(self.validation_data_inputs)
            self.validation_data_inputs = self.validation_data_inputs.astype(np.float32)
            self.validation_data_inputs = self.validation_data_inputs.reshape([self.nr_validation_data, self.get_input_dimension()])
            self.validation_data_inputs = torch.from_numpy(self.validation_data_inputs).float()
            self.validation_data_inputs = self.input_data_scaler.transform(self.validation_data_inputs)

            self.test_data_outputs = np.array(self.test_data_outputs)
            self.test_data_outputs = self.test_data_outputs.astype(np.float32)
            self.test_data_outputs = self.test_data_outputs.reshape([self.nr_test_data, self.get_output_dimension()])
            self.test_data_outputs = torch.from_numpy(self.test_data_outputs).float()
            self.test_data_outputs = self.output_data_scaler.transform(self.test_data_outputs)

            self.validation_data_outputs = np.array(self.validation_data_outputs)
            self.validation_data_outputs = self.validation_data_outputs.astype(np.float32)
            self.validation_data_outputs = self.validation_data_outputs.reshape([self.nr_validation_data, self.get_output_dimension()])
            self.validation_data_outputs = torch.from_numpy(self.validation_data_outputs).float()
            self.validation_data_outputs = self.output_data_scaler.transform(self.validation_data_outputs)

            self.training_data_set = TensorDataset(self.training_data_inputs, self.training_data_outputs)
            self.validation_data_set = TensorDataset(self.validation_data_inputs, self.validation_data_outputs)
            self.test_data_set = TensorDataset(self.test_data_inputs, self.test_data_outputs)



    def __raw_numpy_to_converted_numpy(self, numpy_array, data_type="in", units=None):
        """
        Transforms a raw numpy array containing inputs or outputs into a numpy array with the correct units..
        Parameters
        ----------
        numpy_array : numpy.array
            Array that is to be converted.
        data_type : string
            Either "in" or "out", depending if input or output data is processed (Default: "in").
        units : string
            Units of the data that is processed (Default: None)
        Returns
        -------
        converted_array: numpy.array
            The converted numpy array.
        """

        if data_type == "in":
            if data_type == "in" and self.parameters.descriptors_contain_xyz:
                numpy_array = numpy_array[:, :, :, 3:]
            if units is not None:
                numpy_array *= self.descriptor_calculator.convert_units(1, units)
            return numpy_array
        elif data_type == "out":
            if units is not None:
                numpy_array *= self.target_calculator.convert_units(1, units)
            return numpy_array
        else:
            raise Exception("Please choose either \"in\" or \"out\" for this function.")

    def __converted_numpy_to_scaled_tensor(self, numpy_array, desired_dimensions=None, data_type="in"):
        """
        Transforms a numpy array containing inputs or outputs into a scaled torch tensor,
        i.e. a tensor that can simply be put into a FESL network. No unit conversion.
        Parameters
        ----------
        numpy_array : np.array
            Array that is to be converted to a torch tensor.
        data_type : string
            Either "in" or "out", depending if input or output data is processed.
        Returns
        -------
        converted_tensor: torch.Tensor
            The fully converted and scaled tensor.
        """

        numpy_array = numpy_array.astype(np.float32)
        if desired_dimensions is not None:
            numpy_array = numpy_array.reshape(desired_dimensions)
        numpy_array = torch.from_numpy(numpy_array).float()
        if data_type == "in":
            numpy_array = self.input_data_scaler.transform(numpy_array)
        elif data_type == "out":
            numpy_array = self.output_data_scaler.transform(numpy_array)
        else:
            raise Exception("Please choose either \"in\" or \"out\" for this function.")
        return numpy_array

    def get_input_dimension(self):
        """
        Returns the dimension of the input vector.

        Returns
        -------
        input_dimension : int
            Dimension of the input vector.
        """
        return self.input_dimension

    def get_output_dimension(self):
        """
        Returns the dimension of the output vector.

        Returns
        -------
        output_dimension : int
            Dimension of the output vector.
        """
        return self.output_dimension