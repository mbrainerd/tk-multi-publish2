# Copyright (c) 2017 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

import copy
import os
import traceback
import OpenImageIO as oiio

import mari

import sgtk
from sgtk import TankError
from sgtk.util.filesystem import ensure_folder_exists

HookBaseClass = sgtk.get_hook_baseclass()


MARI_MIPMAPS_ITEM_TYPE_SETTINGS = {
    "mari.channel": {
        "publish_type": "UDIM Image Mipmap",
        "publish_name_template": None,
        "publish_path_template": None,
        "publish_extension": None
    },
    "mari.texture": {
        "publish_type": "UDIM Image Mipmap",
        "publish_name_template": None,
        "publish_path_template": None,
        "publish_extension": None
    }
}

INPUT_PLUGIN_NAME = "Publish Textures"

class MariPublishMipmapsPlugin(HookBaseClass):
    """
    Inherits from PublishFilesPlugin
    """
    @property
    def name(self):
        """
        One line display name describing the plugin
        """
        return "Publish Mipmap from Mari Textures"

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """

        return """
        <p>This plugin publishes mipmaps created from textures exported from 
        the current Mari session to Shotgun.
        Additionally, any files will be exported to the path defined by this plugin's
        configured "Publish Path Template" setting.</p>
        """

    @property
    def settings_schema(self):
        """
        Dictionary defining the settings that this plugin expects to receive
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form::

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default_value": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts
        as part of its environment configuration.
        """
        schema = super(MariPublishMipmapsPlugin, self).settings_schema
        schema["Item Type Filters"]["default_value"] = ["mari.channel", "mari.texture"]
        schema["Item Type Settings"]["values"]["items"]["publish_extension"] = {
            "type": "str",
            "default_value": None,
            "allows_empty": True,
            "description": "Extension (image format) for which mipmap should be created."
        }
        schema["Item Type Settings"]["default_value"] = MARI_MIPMAPS_ITEM_TYPE_SETTINGS
        return schema


    def validate(self, task_settings, item):
        """
        Validates the given item to check that it is ok to publish. Returns a
        boolean to indicate validity.

        :param task_settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        :returns: True if item is valid, False otherwise.
        """
        # TODO: check if Publish Textures plugin was accepted and validated?
        # heavily dependent on that output

        if not super(MariPublishMipmapsPlugin, self).validate(task_settings, item):
            return False

        publish_path = sgtk.util.ShotgunPath.normalize(item.get_property("publish_path"))
        if not self._valid_for_mipmap_multiimage(publish_path):
            error_msg = "Failed to find OIIO plugin or mipmap not supported for extension: %s " \
                        "Validation failed." % os.path.splitext(publish_path)[-1]
            self.logger.error(error_msg)
            return False

        return True


    def publish_files(self, task_settings, item, publish_path):
        """
        Overrides the inherited method to export out session items to the publish_path location.

        :param task_settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        :param publish_path: The output path to publish files to
        """
        publisher = self.parent

        # get the path in a normalized state. no trailing separator, separators
        # are appropriate for current os, no double separators, etc.
        target_path = sgtk.util.ShotgunPath.normalize(publish_path)

        try:
            # ensure the publish folder exists:
            publish_folder = os.path.dirname(target_path)
            ensure_folder_exists(publish_folder)

            source_path_list = self._get_dependency_paths(task_settings, item)
            source_paths_expanded = []

            for source_path in source_path_list:
                # get all associated files if this is a file sequence
                seq_path = publisher.util.get_frame_sequence_path(source_path)
                if seq_path:
                    source_paths_expanded.extend(publisher.util.get_sequence_path_files(source_path))
                else:
                    source_paths_expanded.append(source_path)

            # write mipmaps for each file in the sequence
            mipmap_paths = self.create_mipmaps_for_seq(source_paths_expanded, target_path)

        except Exception as e:
            self.logger.error(traceback.format_exc())
            raise TankError("Failed to publish file for item '%s': %s" % (item.name, str(e)))

        self.logger.debug(
            "Published file for item '%s' to '%s'." % (item.name, target_path)
        )

        return mipmap_paths

    def create_mipmaps_for_seq(self, source_paths, target_seq_path):
        """
        Given a list of files, convert each to a mipmap file and save to target path.

        :param source_paths:        list of source image files
        :param target_seq_path:     path (with an seq field if source is an image sequence)
                                    where the mipmaps should be written

        :returns:                   list of created mipmap paths
        """
        publisher = self.parent
        mipmap_paths = []

        for source_path in source_paths:
            frame = publisher.util.get_frame_number(source_path)
            if frame:
                target_path = publisher.util.get_path_for_frame(target_seq_path, frame)
                if not target_path:
                    # We do not want the conversion of multiple images to overwrite
                    # a single target path. Something is wrong with the configuration.
                    raise TankError("Source path: {} contains a frame number, "
                                    "but target path: {} does not.".format(source_path, target_path))
            else:
                target_path = target_seq_path

            if not self._create_mipmap(source_path, target_path):
                self.logger.warning("Mipmap not created for: {}".format(target_path))
            else:
                mipmap_paths.append(target_path)

        return mipmap_paths

    def _create_mipmap(self, source_path, target_path):
        """
        Use OIIO to convert a given image into a mipmapped image.

        :param source_path: path to source image file
        :param target_path: path to write the mipmapped file to

        :return: bool (success of mipmap creation)
        """
        # cast here as OIIO has an issue with unicode strings (C++ matches types strictly)
        source_path = str(source_path)
        target_path = str(target_path)

        _img_input = oiio.ImageBuf(source_path)
        _target_spec = oiio.ImageSpec(_img_input.spec())
        _target_spec.attribute("maketx:filtername", "lanczos3")

        return oiio.ImageBufAlgo.make_texture(oiio.MakeTxTexture, _img_input, target_path, _target_spec)

    def _valid_for_mipmap_multiimage(self, target_path):
        """
        Check if target object supports mipmapping or multi image capabilities

        :rtype: C{bool}
        :return: Valid mipmap and multiimage target object
        """
        _target = oiio.ImageOutput.create(str(target_path))
        if not _target:
            self.logger.warning("Issues locating OIIO plugin for "
                                "'{0}'".format(os.path.splitext(target_path)[-1]))
            return False
        return any((_target.supports("mipmap"), _target.supports("multiimage")))


    def _get_publish_path(self, task_settings, item):
        """
        Get a publish path for the supplied item with the set extension.

        :param item: The item to determine the publish path for

        :return: A string representing the output path to supply when
            registering a publish for the supplied item

        Extracts the publish path via the configured publish templates
        if possible.
        """
        extension = task_settings.get("publish_extension")
        if not extension:
            raise TankError("publish_extension not set for item: %s" % item.name)

        # override the fields for this plugin, if not already done
        if not "fields" in item.local_properties:
            item.local_properties["fields"] = copy.deepcopy(item.get_property("fields"))
            item.local_properties.fields["extension"] = extension.value

        publish_path = super(MariPublishMipmapsPlugin, self)._get_publish_path(task_settings, item)
        return publish_path

    def _get_publish_name(self, task_settings, item):
        """
        Get a publish name for the supplied item with the set extension.

        :param item: The item to determine the publish name for

        :return: A string representing the published file name

        Uses the path info hook to retrieve the publish name
        via the configured publish templates if possible.
        """
        extension = task_settings.get("publish_extension")
        if not extension:
            raise TankError("publish_extension not set for item: %s" % item.name)

        # override the fields for this plugin, if not already done
        if not "fields" in item.local_properties:
            item.local_properties["fields"] = copy.deepcopy(item.get_property("fields"))
            item.local_properties.fields["extension"] = extension.value

        publish_name = super(MariPublishMipmapsPlugin, self)._get_publish_name(task_settings, item)
        return publish_name

    def _get_dependency_paths(self, task_settings, item):
        """
        Find all dependency paths for the current mipmap.

        :return: List of upstream dependency paths
        """
        # find the key to access INPUT_PLUGIN_NAME's local_properties
        # TODO: is there a better way?
        publish_plugins = self.parent.settings["publish_plugins"]
        key = None
        for plugin_item in publish_plugins:
            if plugin_item["name"] == INPUT_PLUGIN_NAME:
                key = plugin_item["hook"].value
                break

        if not key:
            raise TankError("Publish Mipmaps needs the output of {} to run".format(INPUT_PLUGIN_NAME))

        publish_path_texture = item._local_properties.get(key).get(
            "publish_path")
        return [publish_path_texture]