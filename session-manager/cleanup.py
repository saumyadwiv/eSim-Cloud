import asyncio
from session_store import get_all_sessions, delete_session
from k8s_client import delete_simulation_pod, get_pod_status, list_simulation_pods


async def cleanup_idle_sessions():
    """
    Do kaam karta hai:
    1. Dead pods clean karo (Failed/Unknown)
    2. Orphan pods clean karo — K8s mein hai lekin Redis mein session nahi (TTL expire)
    """
    print("Running cleanup check...")

    # ✅ FIX 1: Active Redis sessions se active pod names nikalo
    try:
        sessions = get_all_sessions()
        active_pod_names = set()

        for session in sessions:
            try:
                pod_name = session["pod_name"]
                namespace = session["namespace"]
                user_id = session["user_id"]
                active_pod_names.add(pod_name)

                status = get_pod_status(pod_name, namespace)

                # Dead pods clean karo
                if status in [None, "Failed", "Succeeded", "Unknown"]:
                    print(f"Dead pod cleanup: {pod_name} for user: {user_id}")
                    delete_simulation_pod(pod_name, namespace)
                    delete_session(user_id)
                    active_pod_names.discard(pod_name)

            except Exception as pod_err:
                print(f"Error processing session for {session.get('user_id')}: {pod_err}")

    except Exception as e:
        print(f"Error reading sessions: {e}")
        return

    # ✅ FIX 2: Orphan pods — K8s mein Running hai lekin Redis TTL expire ho gayi
    try:
        all_k8s_pods = list_simulation_pods()  # K8s se saare simulation pods
        print(f"K8s pods: {all_k8s_pods} | Redis active: {active_pod_names}")

        for pod_name in all_k8s_pods:
            if pod_name not in active_pod_names:
                # Yeh pod Redis mein nahi — TTL expire ho gayi, delete karo
                print(f"Orphan pod cleanup (TTL expired): {pod_name}")
                delete_simulation_pod(pod_name)

    except Exception as e:
        print(f"Error during orphan pod cleanup: {e}")


async def run_cleanup_loop():
    """
    Har 30 seconds mein cleanup — 100s TTL ke saath match karta hai.
    """
    while True:
        try:
            await cleanup_idle_sessions()
        except Exception as e:
            print(f"Critical error in cleanup loop: {e}")

        # ✅ FIX 3: 10s tha pehle — 30s kaafi hai aur CPU waste nahi hoga
        await asyncio.sleep(30)


async def force_cleanup_user(user_id: str):
    """
    Immediately clean up a specific user's session.
    """
    from session_store import get_session
    session = get_session(user_id)

    if session:
        try:
            delete_simulation_pod(session["pod_name"], session["namespace"])
            delete_session(user_id)
            print(f"Force cleaned session for user: {user_id}")
            return True
        except Exception as e:
            print(f"Error during force cleanup for {user_id}: {e}")
            return False
    return False