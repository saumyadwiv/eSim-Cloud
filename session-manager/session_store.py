import redis
import json
import os

# Connect to Redis
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    decode_responses=True
)

SESSION_TTL = 100 # 30 minutes in seconds


def save_session(user_id: str, pod_name: str, namespace: str):
    """
    Save user session info in Redis.
    When user starts simulation — store which pod belongs to them.
    """
    session_data = {
        "pod_name": pod_name,
        "namespace": namespace,
        "user_id": user_id
    }
    redis_client.setex(
        f"session:{user_id}",
        SESSION_TTL,
        json.dumps(session_data)
    )


def get_session(user_id: str):
    """
    Get session info for a user.
    Returns pod details if session exists, None if not found.
    """
    data = redis_client.get(f"session:{user_id}")
    if data:
        return json.loads(data)
    return None


def delete_session(user_id: str):
    """
    Delete session when simulation ends or user closes browser.
    """
    redis_client.delete(f"session:{user_id}")


def get_all_sessions():
    """
    Get all active sessions.
    Used by cleanup service to find idle pods.
    """
    keys = redis_client.keys("session:*")
    sessions = []
    for key in keys:
        data = redis_client.get(key)
        if data:
            sessions.append(json.loads(data))
    return sessions


def refresh_session(user_id: str):
    """
    Reset 30 min timer when user is active.
    Called every time user does something in simulation.
    """
    redis_client.expire(f"session:{user_id}", SESSION_TTL)
# 🆕 NEW: Expired sessions dhundhna — Redis TTL expire ho gayi lekin pod abhi bhi chal raha hai
def get_expired_pod_names():
    """
    Redis mein jo sessions nahi hain lekin pods chal rahe hain —
    unke pod names return karo taaki cleanup kar sake.
    """
    active_keys = redis_client.keys("session:*")
    active_pod_names = set()
    for key in active_keys:
        data = redis_client.get(key)
        if data:
            session = json.loads(data)
            active_pod_names.add(session["pod_name"])
    return active_pod_names  # cleanup service compare karega k8s pods se