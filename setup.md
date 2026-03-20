install Postgres
    1 ` brew install postgresql` 
    2 `brew services start postgresql@18`
    3 `createdb -h 127.0.0.1 -p 5432 -U postgres zivaBooks`
    
Run Migrations
    1. `uv run manage.py makemigrations`
    2. `uv run manage.py migrate`
