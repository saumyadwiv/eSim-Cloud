from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import os
# Mock mode for testing without Kubernetes
MOCK_MODE = os.getenv("MOCK_K8S", "false").lower() == "true"

def load_k8s_config():
    try:
        # Pehle try karega agar in-cluster (K8s ke andar) chal raha hai
        config.load_incluster_config()
        return True
    except config.ConfigException:
        try:
            # Agar local container mein hai, toh humari copy ki hui flat-config use karega
            config.load_kube_config(config_file="/root/.kube/config")
            print("✅ K8s config loaded successfully from /root/.kube/config")
            return True
        except Exception as e:
            print(f"WARNING: No K8s config found, running in mock mode. Error: {e}")
            return False
        # load_k8s_config()
# v1 = client.CoreV1Api()


def create_simulation_pod(user_id: str, namespace: str = "default"):
    """
    Create a new Kubernetes pod for this user.
    Each user gets their own isolated simulation environment.
    """
    pod_name = f"sim-{user_id}" 
    if MOCK_MODE:
        print(f"[MOCK] Created pod: {pod_name}")
        return pod_name

    load_k8s_config()
    v1 = client.CoreV1Api()
    try:
        existing = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        if existing.status.phase in ["Running", "Pending"]:
            print(f"Pod {pod_name} already exists, deleting first...")
            v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
            import time
            time.sleep(2)  # Delete hone do thoda wait karo
    except ApiException as e:
        if e.status != 404:
            raise  # 404 = pod nahi hai, that's fine. Baaki errors raise karo
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                "app": "esim-simulation",
                "user_id": user_id[:8]
            }
        },
        "spec": {
            "containers": [{
                "name": "ngspice",
        "image": "esim-ngspice:latest",
                        "imagePullPolicy": "Never",
                        "command": ["sleep", "infinity"],
                "ports": [{"containerPort": 5000}],
                "resources": {
                    "limits": {
                        "cpu": "500m",
                        "memory": "256Mi"
                    },
                    "requests": {
                        "cpu": "100m",
                        "memory": "128Mi"
                    }
                }
            }],
            "restartPolicy": "Never"
        }
    }
    try:
        v1.create_namespaced_pod(namespace=namespace, body=pod_manifest)
        print(f"Pod {pod_name} created successfully")
        return pod_name
    except ApiException as e:
        print(f"❌ Pod creation failed: {e}")  # 🆕 FIX: Error print karo — silent fail nahi
        raise

def delete_simulation_pod(pod_name: str, namespace: str = "default"):
    """
    Delete pod when simulation ends.
    Frees up resources for other users.
    """
    if MOCK_MODE:
        print(f"[MOCK] Deleted pod: {pod_name}")
        return True
    load_k8s_config()
    v1 = client.CoreV1Api()
    try:
        v1.delete_namespaced_pod(
            name=pod_name,
            namespace=namespace
        )
        return True
    except Exception as e:
        print(f"Error deleting pod {pod_name}: {e}")
        return False


def get_pod_status(pod_name: str, namespace: str = "default"):
    """
    Check if pod is Running, Pending, Failed, or Terminated.
    """
    if MOCK_MODE:
        return "Running"
    
    load_k8s_config()
    v1 = client.CoreV1Api()
    
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        return pod.status.phase  # Running, Pending, Failed, Succeeded
    except ApiException as e:
        if e.status == 404:
            return "Terminated"  # ✅ Pod exist nahi karta — deleted/expired
        return "Unknown"
    except Exception:
        return "Unknown"

def list_simulation_pods(namespace: str = "default"):
    """
    List all active simulation pods.
    Used by cleanup service.
    """
    load_k8s_config()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        namespace=namespace,
        label_selector="app=esim-simulation"
    )
    return [pod.metadata.name for pod in pods.items]
# 🆕 NEW FUNCTION: Expired pods cleanup
def cleanup_expired_pods(active_pod_names: set, namespace: str = "default"):
    """
    K8s mein jo pods hain lekin Redis session nahi hai unhe delete karo.
    Yeh function background task se call hoga har 30 seconds mein.
    """
    if MOCK_MODE:
        return

    load_k8s_config()
    all_running_pods = list_simulation_pods(namespace)

    for pod_name in all_running_pods:
        if pod_name not in active_pod_names:
            print(f"🧹 Cleaning expired pod: {pod_name}")
            delete_simulation_pod(pod_name, namespace)