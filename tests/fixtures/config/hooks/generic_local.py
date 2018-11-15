# Copyright (c) 2018 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()


class GenericLocalPlugin(HookBaseClass):
    """
    This should process the item locally...
    """

    @property
    def name(self):
        return "Publish Plugin that runs LOCALLY"

    @property
    def description(self):
        return "This plugin should process items locally"

    @property
    def settings(self):
        return {}

    @property
    def item_filters(self):
        return ["generic.item"]

    def accept(self, settings, item):
        item.local_properties.plugin_name = "local"
        return {"accepted": True}

    def validate(self, settings, item):
        if "TEST_LOCAL_PROPERTIES" in os.environ:
            # local properties was properly set, so make sure it raises an error.
            # see test_item:test_local_properties_persistance for more info.
            if item.local_properties.plugin_name == "local":
                raise Exception("local_properties was serialized properly.")
        self.logger.debug("Executing local plugin validate.")
        return True

    def publish(self, settings, item):
        self.logger.debug("Executing local plugin publish.")
        pass

    def finalize(self, settings, item):
        self.logger.debug("Executing local plugin finalize.")
        pass
