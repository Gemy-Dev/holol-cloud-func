"""Configuration constants and settings."""
BACKUP_BUCKET = "medical-advisor-bd734-backups"
COLLECTIONS_TO_BACKUP = [
    "users", "products", "clients", "tasks", "plans", 
    "departments", "specialties", "procedures", "companies",
    "notifications", "reports", "analytics"
]

