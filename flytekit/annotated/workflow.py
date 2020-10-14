import inspect
import datetime
from typing import Dict, Any, Callable, Union, Tuple

from flytekit import engine as flytekit_engine, logger
from flytekit.annotated.context_manager import FlyteContext, ExecutionState
from flytekit.annotated.interface import transform_signature_to_interface, transform_interface_to_typed_interface, \
    transform_inputs_to_parameters
from flytekit.annotated.promise import Promise, create_task_output
from flytekit.common import constants as _common_constants
from flytekit.common.workflow import SdkWorkflow as _SdkWorkflow
from flytekit.models import literals as _literal_models, types as _type_models
from flytekit.models.core import identifier as _identifier_model, workflow as _workflow_model
from flytekit.common.exceptions import user as _user_exceptions
from flytekit.common import nodes as _nodes
from flytekit.common import promise as _common_promise


class Workflow(object):
    """
    When you assign a name to a node.
    * Any upstream node that is not assigned, recursively assign
    * When you get the call to the constructor, keep in mind there may be duplicate nodes, because they all should
      be wrapper nodes.
    """

    def __init__(self, workflow_function: Callable):
        self._name = workflow_function.__name__
        self._workflow_function = workflow_function
        self._native_interface = transform_signature_to_interface(inspect.signature(workflow_function))
        self._interface = transform_interface_to_typed_interface(self._native_interface)
        # This will get populated on compile only
        self._sdk_workflow = None
        # TODO do we need this - can this not be in launchplan only?
        #    This can be in launch plan only, but is here only so that we don't have to re-evaluate. Or
        #    we can re-evaluate.
        self._input_parameters = None

    @property
    def function(self):
        return self._workflow_function

    @property
    def name(self):
        return self._name

    @property
    def interface(self):
        return self._interface

    def compile(self):
        # TODO should we even define it here?
        self._input_parameters = transform_inputs_to_parameters(self._native_interface)
        all_nodes = []
        ctx = FlyteContext.current_context()
        with ctx.new_compilation_context() as comp_ctx:
            # Fill in call args by constructing input bindings
            input_kwargs = {
                k: _type_models.OutputReference(_common_constants.GLOBAL_INPUT_NODE_ID, k)
                for k in self._native_interface.inputs.keys()
            }
            workflow_outputs = self._workflow_function(**input_kwargs)
            all_nodes.extend(comp_ctx.compilation_state.nodes)

        # Iterate through the workflow outputs
        #  Get the outputs and use them to construct the old Output objects
        #    promise.NodeOutputs (let's just focus on this one first for POC)
        #    or Input objects from above in the case of a passthrough value
        #    or outputs can be like 5, or 'hi'
        # These should line up with the output input argument
        # TODO: Add length checks.
        bindings = []
        output_names = list(self._native_interface.outputs.keys())
        if len(output_names) > 0:
            for i, out in enumerate(workflow_outputs):
                output_name = output_names[i]
                # TODO: Check that the outputs returned type match the interface.
                # output_literal_type = out.literal_type
                # logger.debug(f"Got output wrapper: {out}")
                # logger.debug(f"Var name {output_name} wf output name {outputs[i]} type: {output_literal_type}")
                binding_data = _literal_models.BindingData(promise=out)
                bindings.append(_literal_models.Binding(var=output_name, binding=binding_data))

        # TODO: Again, at this point, we should be able to identify the name of the workflow
        workflow_id = _identifier_model.Identifier(_identifier_model.ResourceType.WORKFLOW,
                                                   "proj", "dom", "moreblah", "1")

        # Create a FlyteWorkflow object. We call this like how promote_from_model would call this, by ignoring the
        # fancy arguments and supplying just the raw elements manually. Alternatively we can construct the
        # WorkflowTemplate object, and then call promote_from_model.
        self._sdk_workflow = _SdkWorkflow(inputs=None, outputs=None, nodes=all_nodes, id=workflow_id, metadata=None,
                                          metadata_defaults=None, interface=self._interface, output_bindings=bindings)
        if not output_names:
            return None
        if len(output_names) == 1:
            return bindings[0]
        return tuple(bindings)

    def _local_execute(self, ctx: FlyteContext, **kwargs) -> Union[Tuple[Promise], Promise, None]:
        """
        Performs local execution of a workflow. kwargs are expected to be Promises for the most part (unless,
        someone has hardcoded in my_wf(input_1=5) or something).
        :param ctx: The FlyteContext
        :param nested: boolean that indicates if this is a nested workflow execution (subworkflow)
        :param kwargs: parameters for the workflow itself
        """
        logger.info(f"Executing Workflow {self._name}, ctx{ctx.execution_state.Mode}")

        # This is done to support the invariant that Workflow local executions always work with Promise objects
        # holding Flyte literal values. Even in a wf, a user can call a sub-workflow with a Python native value.
        for k, v in kwargs.items():
            if not isinstance(v, Promise):
                kwargs[k] = Promise(
                    var=k, val=flytekit_engine.python_value_to_idl_literal(ctx, v, self.interface.inputs[k].type))

        # TODO: function_outputs are all assumed to be Promise objects produced by task calls, but can they be
        #   other things as well? What if someone just returns 5? Should we disallow this?
        function_outputs = self._workflow_function(**kwargs)

        output_names = list(self.interface.outputs.keys())
        if len(output_names) == 0:
            if function_outputs is None:
                return None
            else:
                raise Exception("something returned from wf but shouldn't have outputs")

        if len(output_names) != len(function_outputs):
            # Length check, clean up exception
            raise Exception(f"Length difference {len(output_names)} {len(function_outputs)}")

        # This recasts the Promises provided by the outputs of the workflow's tasks into the correct output names
        # of the workflow itself
        vals = [Promise(var=output_names[idx], val=function_outputs[idx].val) for idx, promise in
                enumerate(function_outputs)]
        return create_task_output(vals)

    def __call__(self, *args, **kwargs):
        if len(args) > 0:
            raise Exception('not allowed')

        ctx = FlyteContext.current_context()

        # Handle subworkflows in compilation
        if ctx.compilation_state is not None:
            return self._create_and_link_node(ctx, **kwargs)

        elif ctx.execution_state is not None and ctx.execution_state.mode == ExecutionState.Mode.LOCAL_WORKFLOW_EXECUTION:
            # We are already in a local execution, just continue the execution context
            return self._local_execute(ctx, **kwargs)

        # When someone wants to run the workflow function locally. Assume that the inputs given are given as Python
        # native values. _local_execute will always translate Python native literals to Flyte literals, so no worries
        # there, but it'll return Promise objects.
        else:
            # Run some sanity checks
            # Even though the _local_execute call generally expects inputs to be Promises, we don't have to do the
            # conversion here in this loop. The reason is because we don't prevent users from specifying inputs
            # as direct scalars, which means there's another Promise-generating loop inside _local_execute too
            for k, v in kwargs.items():
                if k not in self.interface.inputs:
                    raise ValueError(f"Received unexpected keyword argument {k}")
                if isinstance(v, Promise):
                    raise ValueError(
                        f"Received a promise for a workflow call, when expecting a native value for {k}")

            with ctx.new_execution_context(mode=ExecutionState.Mode.LOCAL_WORKFLOW_EXECUTION) as ctx:
                result = self._local_execute(ctx, **kwargs)

            if result is None:
                return None

            if isinstance(result, Promise):
                literals = {result.var: result.val}
            else:
                literals = {}
                for prom in result:
                    if not isinstance(prom, Promise):
                        raise Exception("should be promises")

                    literals[prom.var] = prom.val

            # unpack result
            return flytekit_engine.idl_literal_map_to_python_value(ctx, _literal_models.LiteralMap(
                literals=literals))

    def _create_and_link_node(self, ctx: FlyteContext, *args, **kwargs):
        """
        This method is used to create a node representing a subworkflow call in a workflow. It should closely mirror
        the _compile function found in Task.
        """

        # TODO: Add handling of defaults
        used_inputs = set()
        bindings = []

        for k in sorted(self.interface.inputs):
            var = self.interface.inputs[k]
            if k not in kwargs:
                raise _user_exceptions.FlyteAssertion(
                    "Input was not specified for: {} of type {}".format(k, var.type)
                )
            bindings.append(flytekit_engine.binding_from_python_std(ctx, k, var.type, kwargs[k]))
            used_inputs.add(k)

        extra_inputs = used_inputs ^ set(kwargs.keys())
        if len(extra_inputs) > 0:
            raise _user_exceptions.FlyteAssertion(
                "Too many inputs were specified for the interface.  Extra inputs were: {}".format(extra_inputs)
            )

        # Detect upstream nodes
        upstream_nodes = [input_val.ref.sdk_node for input_val in kwargs.values() if isinstance(input_val, Promise)]

        sdk_node = _nodes.SdkNode(
            # TODO
            id=f"node-{len(ctx.compilation_state.nodes)}",
            metadata=_workflow_model.NodeMetadata(self._name, datetime.timedelta(),
                                                   _literal_models.RetryStrategy(0)),
            bindings=sorted(bindings, key=lambda b: b.var),
            upstream_nodes=upstream_nodes,
            sdk_workflow=self._sdk_workflow
        )
        ctx.compilation_state.nodes.append(sdk_node)

        # Create a node output object for each output, they should all point to this node of course.
        # TODO: Again, we need to be sure that we end up iterating through the output names in the correct order
        #  investigate this and document here.
        node_outputs = []
        for output_name, output_var_model in self.interface.outputs.items():
            # TODO: If node id gets updated later, we have to make sure to update the NodeOutput model's ID, which
            #  is currently just a static str
            node_outputs.append(
                Promise(output_name, _common_promise.NodeOutput(sdk_node=sdk_node, sdk_type=None, var=output_name)))
        # Don't print this, it'll crash cuz sdk_node._upstream_node_ids might be None, but idl code will break

        return create_task_output(node_outputs)


def workflow(_workflow_function=None):
    # Unlike for tasks, where we can determine the entire structure of the task by looking at the function's signature,
    # workflows need to have the body of the function itself run at module-load time. This is because the body of the
    # workflow is what expresses the workflow structure.
    def wrapper(fn):
        # TODO: Again, at this point, we should be able to identify the name of the workflow
        workflow_id = _identifier_model.Identifier(_identifier_model.ResourceType.WORKFLOW,
                                                   "proj", "dom", "moreblah", "1")
        workflow_instance = Workflow(fn)
        workflow_instance.compile()
        workflow_instance.id = workflow_id

        return workflow_instance

    if _workflow_function:
        return wrapper(_workflow_function)
    else:
        return wrapper