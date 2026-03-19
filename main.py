from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import docker
import subprocess
import uuid
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

instances = {}
docker_client = docker.from_env()


class InstanceConfig(BaseModel):
    type: str
    os: str
    cpu: int
    ram: int
    disk: int
    name: Optional[str] = None

def create_container(config, instance_id):
    try:
        container = docker_client.containers.run(
            image=config.os,
            name=config.name or f"instance_{instance_id}",
            detach=True,
            mem_limit=f"{config.ram}m",
            nano_cpus=config.cpu * 1_000_000_000,
            stdin_open=True,
            tty=True
        )
        return {
            "id": instance_id,
            "name": config.name or f"container_{instance_id}",
            "type": "container",
            "os": config.os,
            "status": "running",
            "created_at": datetime.now(),
            "docker_id": container.id,
            "cpu_limit": config.cpu,
            "ram_limit": config.ram,
            "disk_limit": config.disk
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def create_vm(config, instance_id):
    try:
        disk_path = f"/home/niwelk/vm_images/instance_{instance_id}.qcow2"
        subprocess.run(["qemu-img", "create", "-f", "qcow2", disk_path, f"{config.disk}G"], check=True)

        vm_command = [
            "qemu-system-x86_64",
            "-name", f"instance_{instance_id}",
            "-m", str(config.ram),
            "-smp", str(config.cpu),
            "-drive", f"file={disk_path},format=qcow2",
            "-cdrom", f"/iso/{config.os}.iso",
            "-netdev", "user,id=net0",
            "-device", "e1000,netdev=net0",
            "-daemonize"
        ]
        subprocess.run(vm_command, check=True)

        return {
            "id": instance_id,
            "name": config.name or f"vm_{instance_id}",
            "type": "vm",
            "os": config.os,
            "status": "running",
            "created_at": datetime.now(),
            "disk_path": disk_path,
            "cpu_limit": config.cpu,
            "ram_limit": config.ram,
            "disk_limit": config.disk
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/create")
async def create_instance(config: InstanceConfig):
    instance_id = str(uuid.uuid4())[:8]

    if config.type == "container":
        instances[instance_id] = create_container(config, instance_id)
        return {"status": "success", "instance_id": instance_id, "message": "Контейнер создан"}

    elif config.type == "vm":
        instances[instance_id] = create_vm(config, instance_id)
        return {"status": "success", "instance_id": instance_id, "message": "ВМ создана"}

    raise HTTPException(status_code=400, detail="Неверный тип")


@app.get("/api/list")
async def list_instances():
    return {"instances": list(instances.values())}


@app.post("/api/stop/{instance_id}")
async def stop_instance(instance_id: str):
    if instance_id not in instances:
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    instance = instances[instance_id]

    if instance["type"] == "container":
        docker_client.containers.get(instance["docker_id"]).stop()
    elif instance["type"] == "vm":
        subprocess.run(["pkill", "-f", f"instance_{instance_id}"])

    instance["status"] = "stopped"
    return {"status": "success", "message": "Инстанс остановлен"}


@app.post("/api/start/{instance_id}")
async def start_instance(instance_id: str):
    if instance_id not in instances:
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    instance = instances[instance_id]

    if instance["type"] == "container":
        docker_client.containers.get(instance["docker_id"]).start()
        instance["status"] = "running"
        return {"status": "success", "message": "Контейнер запущен"}

    return {"status": "error", "message": "Запуск ВМ временно не поддерживается"}


@app.delete("/api/delete/{instance_id}")
async def delete_instance(instance_id: str):
    if instance_id not in instances:
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    instance = instances[instance_id]

    if instance["type"] == "container":
        docker_client.containers.get(instance["docker_id"]).remove(force=True)
    elif instance["type"] == "vm":
        subprocess.run(["rm", "-f", instance["disk_path"]])
        subprocess.run(["pkill", "-f", f"instance_{instance_id}"])

    del instances[instance_id]
    return {"status": "success", "message": "Инстанс удален"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)