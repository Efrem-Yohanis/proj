# node_runner.py
import asyncio
import importlib.util
import sys
import traceback
from datetime import datetime
from django.utils.timezone import now as timezone_now
from channels.db import database_sync_to_async

class NodeRunner:
    """Executes NodeVersion scripts with async support"""

    @staticmethod
    async def run_standalone_async(node_version_id, subnode_id=None, params=None, triggered_by="system"):
        """Async version of run_standalone"""
        from .models import NodeExecution
        
        # Create execution record using sync_to_async
        execution = await database_sync_to_async(NodeExecution.objects.create)(
            version_id=node_version_id,
            status="running",
            triggered_by=triggered_by,
            started_at=timezone_now(),
            log="",
        )

        async def _log(msg: str):
            timestamped = f"[{datetime.utcnow().isoformat()}] {msg}"
            await database_sync_to_async(execution.__setattr__)("log", (execution.log or "") + timestamped + "\n")
            await database_sync_to_async(execution.save)(update_fields=["log"])

        try:
            await _log(f"Starting execution {execution.id}")
            
            # Run the actual node execution in a thread pool
            await asyncio.to_thread(
                NodeRunner._execute_node_sync,
                node_version_id,
                subnode_id,
                params,
                lambda msg: asyncio.run(_log(msg))
            )
            
            await _log("Node execution completed successfully")
            await database_sync_to_async(execution.__setattr__)("status", "completed")
            
        except Exception as e:
            tb = traceback.format_exc()
            await _log(f"Execution error: {str(e)}\n{tb}")
            await database_sync_to_async(execution.__setattr__)("status", "failed")
            
        finally:
            await database_sync_to_async(execution.__setattr__)("completed_at", timezone_now())
            await database_sync_to_async(execution.save)()

        return execution

    @staticmethod
    def _execute_node_sync(node_version_id, subnode_id, params, log_callback):
        """Synchronous node execution (run in thread pool)"""
        from .models import NodeVersion
        
        try:
            nv = NodeVersion.objects.get(id=node_version_id)
            log_callback(f"Starting NodeVersion {nv.family.name} v{nv.version}")
            
            script_path = nv.script.path if nv.script else None
            if not script_path:
                raise ValueError("NodeVersion has no script file")

            # Load script dynamically
            spec = importlib.util.spec_from_file_location(f"node_{nv.id}", script_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"node_{nv.id}"] = module
            spec.loader.exec_module(module)

            # Merge parameters
            run_params = {p.parameter.key: p.value for p in nv.parameters.select_related("parameter").all()}
            
            if subnode_id:
                from flow_builder_app.subnode.models import SubNodeParameterValue
                sn_params = {
                    sp.parameter.key: sp.value 
                    for sp in SubNodeParameterValue.objects.filter(subnode_id=subnode_id).select_related("parameter")
                }
                run_params.update(sn_params)
            
            if params:
                run_params.update(params)

            log_callback(f"Executing script with parameters: {run_params}")
            executed = False

            # Try to find and execute run function
            if hasattr(module, "run") and callable(module.run):
                module.run(**run_params)
                executed = True
                log_callback("Executed run() function successfully")
            else:
                # Look for classes with run method
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type):
                        instance = attr()
                        for key, value in run_params.items():
                            if hasattr(instance, key):
                                setattr(instance, key, value)
                        if hasattr(instance, "run") and callable(getattr(instance, "run")):
                            instance.run()
                            executed = True
                            log_callback(f"Executed {attr_name}.run() successfully")
                            break

            if not executed:
                log_callback("No run() function or class with run() method found in script")

        except Exception as e:
            tb = traceback.format_exc()
            log_callback(f"Execution error: {str(e)}\n{tb}")
            raise