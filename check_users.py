from app import app, db, User

# We must push the app context to access the database
with app.app_context():
    users = User.query.all()
    print("-" * 30)
    print(f"👥 TOTAL USERS: {len(users)}")
    print("-" * 30)
    
    for user in users:
        print(f"ID: {user.id} | Name: {user.name} | Email: {user.email}")
        print(f"Password Hash: {user.password[:20]}...") # Only show first 20 chars for security
        print("-" * 30)