import time
import sys
import os

# Adjust sys.path to resolve local python modules inside the container
sys.path.append("/app")
sys.path.append("/")

try:
    import eccsa_db as db
    from views.vales_oxxogas import fetch_and_sync_oxxogas_emails
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Core Import Error: {e}")
    sys.exit(1)

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting OxxoGas automated sync background service...")

while True:
    try:
        # 1. Fetch all users who have the OxxoGas allowed permission flag active (AccesoValesOxxoGas)
        users = db.get_all_hub_users()
        target_users = [u for u in users if u.get("AccesoValesOxxoGas")]

        if not target_users:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No users with AccesoValesOxxoGas permission found.")
        else:
            for user in target_users:
                user_id = user["Id"]
                user_name = user["Nombre"]
                user_email = user["Email"]
                
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Processing sync for user {user_name} ({user_email}) [ID: {user_id}]...")
                
                # 2. Call the sync handler providing the loop user_id
                count, msg = fetch_and_sync_oxxogas_emails(user_id=user_id)
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sync finished. Count: {count}. Status: {msg}")

    except Exception as err:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error in background loop: {err}")

    # Wait for 1 hour before scanning again
    time.sleep(3600)
