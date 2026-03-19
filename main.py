# =========================================================
# БЛОК 1: ИМПОРТЫ — подключаем нужные библиотеки
# =========================================================

from fastapi import FastAPI, HTTPException          # FastAPI для создания API, HTTPException для ошибок
from fastapi.middleware.cors import CORSMiddleware  # CORS — чтобы браузер разрешал запросы с другого порта
from pydantic import BaseModel                       # Для описания формата данных (какие поля ожидаем)
from typing import Optional                           # Для полей, которые могут быть пустыми (например, имя)
import docker                                         # Чтобы управлять Docker контейнерами
import subprocess                                      # Чтобы запускать команды в терминале (QEMU, pkill, rm)
import uuid                                            # Генерировать уникальные ID для экземпляров
from datetime import datetime                          # Для работы с датой и временем (когда создан)

# =========================================================
# БЛОК 2: НАСТРОЙКА FASTAPI
# =========================================================

app = FastAPI()  # Создаём само приложение FastAPI

# Разрешаем запросы с любых адресов (нужно для Streamlit на порту 8501)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Разрешить с любого адреса
    allow_credentials=True,        # Разрешить передачу куки
    allow_methods=["*"],           # Разрешить все методы (GET, POST, DELETE...)
    allow_headers=["*"],           # Разрешить все заголовки
)

# =========================================================
# БЛОК 3: ХРАНИЛИЩЕ ДАННЫХ (в памяти)
# =========================================================

instances = {}                     # Словарь, где хранятся все созданные экземпляры (ключ = ID, значение = данные)
docker_client = docker.from_env()  # Подключение к Docker (чтобы управлять контейнерами)

# =========================================================
# БЛОК 4: МОДЕЛИ ДАННЫХ (что приходит от пользователя)
# =========================================================

class InstanceConfig(BaseModel):
    """Описывает, какие данные приходят от пользователя при создании"""
    type: str          # "vm" или "container"
    os: str            # Операционная система (ubuntu:latest, centos:7, alpine-3.16 и т.д.)
    cpu: int           # Количество ядер CPU
    ram: int           # Оперативная память в MB
    disk: int          # Место на диске в GB
    name: Optional[str] = None  # Имя экземпляра (необязательное поле)

# =========================================================
# БЛОК 5: ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СОЗДАНИЯ
# =========================================================

def create_container(config, instance_id):
    """Создаёт Docker контейнер и возвращает словарь с его данными"""
    try:
        # Запускаем контейнер через Docker
        container = docker_client.containers.run(
            image=config.os,                          # Образ (ubuntu:latest, centos:7...)
            name=config.name or f"instance_{instance_id}",  # Имя контейнера (или автоматическое)
            detach=True,                               # Запуск в фоне
            mem_limit=f"{config.ram}m",                # Ограничение памяти
            nano_cpus=config.cpu * 1_000_000_000,      # Ограничение CPU (в наноядрах)
            stdin_open=True,                            # Открыть STDIN
            tty=True                                    # Выделить псевдо-терминал
        )
        
        # Возвращаем словарь с данными о контейнере для сохранения
        return {
            "id": instance_id,
            "name": config.name or f"container_{instance_id}",
            "type": "container",
            "os": config.os,
            "status": "running",
            "created_at": datetime.now(),
            "docker_id": container.id,          # ID контейнера в Docker
            "cpu_limit": config.cpu,
            "ram_limit": config.ram,
            "disk_limit": config.disk
        }
    except Exception as e:
        # Если ошибка — выбрасываем HTTP-исключение (500 Internal Server Error)
        raise HTTPException(status_code=500, detail=str(e))


def create_vm(config, instance_id):
    """Создаёт виртуальную машину через QEMU и возвращает словарь с её данными"""
    try:
        # Путь к файлу виртуального диска
        disk_path = f"/home/niwelk/vm_images/instance_{instance_id}.qcow2"
        
        # Создаём образ диска через qemu-img
        subprocess.run([
            "qemu-img", "create", "-f", "qcow2", disk_path, f"{config.disk}G"
        ], check=True)
        
        # Команда для запуска QEMU
        vm_command = [
            "qemu-system-x86_64",
            "-name", f"instance_{instance_id}",
            "-m", str(config.ram),                     # Память
            "-smp", str(config.cpu),                    # Ядра CPU
            "-drive", f"file={disk_path},format=qcow2", # Подключаем диск
            "-cdrom", f"/iso/{config.os}.iso",          # Подключаем ISO (установочный образ)
            "-netdev", "user,id=net0",                   # Сеть
            "-device", "e1000,netdev=net0",              # Сетевой адаптер
            "-daemonize"                                  # Запуск в фоне
        ]
        
        # Запускаем QEMU
        subprocess.run(vm_command, check=True)
        
        # Возвращаем словарь с данными о ВМ
        return {
            "id": instance_id,
            "name": config.name or f"vm_{instance_id}",
            "type": "vm",
            "os": config.os,
            "status": "running",
            "created_at": datetime.now(),
            "disk_path": disk_path,                     # Путь к файлу диска (чтобы потом удалить)
            "cpu_limit": config.cpu,
            "ram_limit": config.ram,
            "disk_limit": config.disk
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# БЛОК 6: ЭНДПОИНТЫ API (точки входа, которые вызываются из интерфейса)
# =========================================================

@app.post("/api/create")
async def create_instance(config: InstanceConfig):
    """Создаёт новый экземпляр (контейнер или ВМ)"""
    # Генерируем уникальный ID (первые 8 символов UUID)
    instance_id = str(uuid.uuid4())[:8]

    # В зависимости от типа вызываем нужную функцию
    if config.type == "container":
        instances[instance_id] = create_container(config, instance_id)
        return {"status": "success", "instance_id": instance_id, "message": "Контейнер создан"}

    elif config.type == "vm":
        instances[instance_id] = create_vm(config, instance_id)
        return {"status": "success", "instance_id": instance_id, "message": "ВМ создана"}

    # Если тип не распознан — ошибка 400 (Bad Request)
    raise HTTPException(status_code=400, detail="Неверный тип")


@app.get("/api/list")
async def list_instances():
    """Возвращает список всех созданных экземпляров"""
    return {"instances": list(instances.values())}


@app.post("/api/stop/{instance_id}")
async def stop_instance(instance_id: str):
    """Останавливает работающий экземпляр"""
    # Проверяем, есть ли такой ID
    if instance_id not in instances:
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    instance = instances[instance_id]  # Получаем данные экземпляра

    # Останавливаем в зависимости от типа
    if instance["type"] == "container":
        # Останавливаем контейнер через Docker
        docker_client.containers.get(instance["docker_id"]).stop()
    elif instance["type"] == "vm":
        # Убиваем процесс QEMU по имени (содержит instance_id)
        subprocess.run(["pkill", "-f", f"instance_{instance_id}"])

    # Меняем статус в нашем хранилище
    instance["status"] = "stopped"
    return {"status": "success", "message": "Инстанс остановлен"}


@app.post("/api/start/{instance_id}")
async def start_instance(instance_id: str):
    """Запускает остановленный экземпляр"""
    if instance_id not in instances:
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    instance = instances[instance_id]

    # Запускаем контейнер (для ВМ пока не реализовано)
    if instance["type"] == "container":
        docker_client.containers.get(instance["docker_id"]).start()
        instance["status"] = "running"
        return {"status": "success", "message": "Контейнер запущен"}

    # Для ВМ возвращаем ошибку (можно добавить позже)
    return {"status": "error", "message": "Запуск ВМ временно не поддерживается"}


@app.delete("/api/delete/{instance_id}")
async def delete_instance(instance_id: str):
    """
    Удаляет экземпляр (контейнер или ВМ)
    
    - для контейнера: удаляет через Docker
    - для ВМ: удаляет файл диска и убивает процесс
    - удаляет запись из нашего словаря instances
    """
    # 1. Проверяем, существует ли такой ID в нашем хранилище
    if instance_id not in instances:
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    # 2. Получаем данные экземпляра из словаря
    instance = instances[instance_id]  # ← ЭТО ЗАЧЕМ: чтобы узнать тип и параметры

    # 3. В зависимости от типа выполняем разные действия
    if instance["type"] == "container":
        # Получаем контейнер по его Docker ID и принудительно удаляем
        docker_client.containers.get(instance["docker_id"]).remove(force=True)  # force=True — даже если запущен
    
    elif instance["type"] == "vm":
        # Удаляем файл виртуального диска (команда rm -f)
        subprocess.run(["rm", "-f", instance["disk_path"]])  # ← ЧТО: удаляет файл .qcow2
        
        # Убиваем процесс QEMU, если он ещё запущен
        subprocess.run(["pkill", "-f", f"instance_{instance_id}"])  # ← ЧТО: ищет процессы с этим именем и убивает

    # 4. Удаляем запись из нашего словаря instances
    del instances[instance_id]  # ← ЭТО ЗАЧЕМ: чтобы экземпляр исчез из списка в интерфейсе

    # 5. Возвращаем успешный ответ
    return {"status": "success", "message": "Инстанс удален"}


# =========================================================
# БЛОК 7: ЗАПУСК СЕРВЕРА (если файл запущен напрямую)
# =========================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)  # Запускаем сервер на порту 8000
