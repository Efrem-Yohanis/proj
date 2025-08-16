# Bash (Linux/macOS) - multipart file upload
curl -v -X PATCH "http://127.0.0.1:8000/api/node-families/<FAMILY_ID>/versions/<VERSION>/script/" \
  -H "Authorization: Token <YOUR_TOKEN>" \
  -F "script=@/full/path/to/db_source.py"

# PowerShell (Windows) - multipart file upload
# Use single quotes around URL in PowerShell if needed, escape backticks for line continuation
curl -v -X PATCH 'http://127.0.0.1:8000/api/node-families/<FAMILY_ID>/versions/<VERSION>/script/' `
  -H 'Authorization: Token <YOUR_TOKEN>' `
  -F "script=@C:\Users\Lenovo\Downloads\db_source.py"

# Alternative: send script as raw JSON text (no file upload)
curl -v -X PATCH "http://127.0.0.1:8000/api/node-families/<FAMILY_ID>/versions/<VERSION>/script/" \
  -H "Authorization: Token <YOUR_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"script_text":"# python code\nprint(\"hello\")\n"}'
