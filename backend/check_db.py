from database import get_connection

with get_connection() as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), session_user, current_user;"
        )
        result = cursor.fetchone()

        print("Database:", result["current_database"])
        print("Login user:", result["session_user"])
        print("Active role:", result["current_user"])