"""Business operations. API endpoints validate input and permissions, then call these functions.
Every function here runs inside the request transaction and either completes fully or raises an ApiError."""
