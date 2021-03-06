#   Copyright 2011 Red Hat, Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import libxml2
import logging
import props
import sys
import threading
from imgfac.Template import Template
from imgfac.ReservationManager import ReservationManager
from imgfac.ApplicationConfiguration import ApplicationConfiguration

class BuildJob(object):

    template = props.prop("_template", "The template property.")
    target = props.prop("_target", "The target property.")
    image_id = props.prop("_image_id", "The UUID of the image.")
    build_id = props.prop("_build_id", "The UUID of the build.")
    provider = props.prop("_provider", "The provider being pushed to.")
    status = props.prop("_status", "The status property.")
    percent_complete = props.prop("_percent_complete", "The percent_complete property.")
    new_image_id = props.prop("_new_image_id" "The image property.")
    operation = props.prop("_operation", "Operation of the builder. ie 'build' or 'push'")
    builder_thread = props.prop("_builder_thread", "Thread object doing the build or push")
    target_image_id = props.prop("_target_image_id", "UUID of the target image being pushed.")

    @property
    def provider_account_identifier(self):
        """The property provider_account_identifier"""
        return self._builder.provider_account_identifier

    def __init__(self, template, target, image_id = '', build_id = ''):
        super(BuildJob, self).__init__()

        self.log = logging.getLogger('%s.%s' % (__name__, self.__class__.__name__))

        self.appconfig = ApplicationConfiguration().configuration
        self.template = template if isinstance(template, Template) else Template(template)
        self.target = target
        self.image_id = image_id
        self.build_id = build_id
        self.provider = None
        self.status = "New"
        self.percent_complete = 0
        self._watcher = None
        self.target_image_id = None
        self._provider_account_identifier = None

        self._builder = self._get_builder()
        self._builder.delegate = self

        self.new_image_id = self._builder.new_image_id
        self.operation = None
        self.builder_thread = None

    def build_image(self, watcher=None):
        self._watcher = watcher
        kwargs = dict(build_id=self.build_id)
        self._start_builder_thread("build_image", arg_dict=kwargs)
        self.operation = 'build'

    def push_image(self, target_image_id, provider, credentials, watcher=None):
        self._watcher = watcher
        self.provider = provider
        self.target_image_id = target_image_id
        kwargs = dict(target_image_id=target_image_id, provider=provider, credentials=credentials)
        self._start_builder_thread("push_image", arg_dict=kwargs)
        self.operation = 'push'

    def abort(self):
        self._builder.abort()

#### IBuilderDelegate methods ####
    def _queue_for_builder(self, builder, status):
        ec2 = builder.target == 'ec2'
        upload = self.appconfig['ec2_build_style'] == 'upload'
        ebs = self.appconfig['ec2_ami_type'] == 'ebs'
        if(status == 'BUILDING'):
            if((not ec2) or upload):
                return 'local'
        elif(status == 'PUSHING'):
            if((ec2 and ebs and upload) or (ec2 and (not upload))):
                return 'ec2'
        return None

    def builder_will_update_status(self, builder, original_status, new_status):
        if(new_status in ('BUILDING','PUSHING')):
            qname = self._queue_for_builder(builder, new_status)
            self.log.debug("%s for %s about to enter %s queue..." % (builder.new_image_id, builder.target, qname))
            ReservationManager().enter_queue(qname)
        return new_status

    def builder_did_update_status(self, builder, old_status, new_status):
        self.log.debug("Builder (%s) changed status from %s to %s" % (builder.new_image_id, old_status, new_status))
        if((new_status in ('COMPLETED','FAILED')) and (old_status in ('BUILDING','PUSHING'))):
            qname = self._queue_for_builder(builder, old_status)
            self.log.debug("%s for %s about to exit %s queue..." % (builder.new_image_id, builder.target, qname))
            ReservationManager().exit_queue(qname)

        self.status = new_status
        if self.status == "COMPLETED" and self._watcher:
            self._watcher.completed()
            self._watcher = None

    def builder_did_update_percentage(self, builder, original_percentage, new_percentage):
        self.log.debug("Builder (%s) changed percent complete from %s to %s" % (builder.new_image_id, original_percentage, new_percentage))
        self.percent_complete = new_percentage

    def builder_did_fail(self, builder, failure_type, failure_info):
        self.log.debug("Builder (%s) reported BUILD FAILED: %s - %s" % (builder.new_image_id, failure_type, failure_info))
#### End Delegate methods ####

    def _get_builder(self):
        if self.target == "mock":
            # If target is mock always run mock builder regardless of template
            os_name = "Mock"
            target_name = ""
        else:
            nodes = libxml2.parseDoc(self.template.xml).xpathEval('/template/os/name')
            if len(nodes) == 0:
                raise Exception, "No OS name defined in the template"
            elif len(nodes) > 1:
                raise Exception, "Multiple OS names defined in the template"
            # Change RHEL-6 to RHEL6, etc.
            os_name = nodes[0].content.translate(None, '-')
            target_name = self.target + "_"

        class_name = "%s_%sBuilder" % (os_name, target_name)
        module_name = "imgfac.builders.%s" % (class_name, )
        __import__(module_name)
        builder_class = getattr(sys.modules[module_name], class_name)

        return builder_class(self.template, self.target)

    def _start_builder_thread(self, method_name, arg_dict):
        thread_name = "%s" % (str(self.new_image_id)[0:8])
        # using args to pass the method we want to call on the target object.
        self.builder_thread = threading.Thread(target = self._builder, name=thread_name, args=(method_name), kwargs=arg_dict)
        self.builder_thread.start()
