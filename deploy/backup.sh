#!/bin/bash
# Backup completo: database (per il ripristino) + Excel (leggibile da chiunque).
# Le copie vanno su 3 destinazioni: disco del Mac mini, NAS, cloud (cartella sincronizzata).
set -euo pipefail
cd /opt/gestionale-asta
export ASTA_BACKUP_PASSWORD="$(security find-generic-password -s gestionale-asta-backup -w)"
export ASTA_FIELD_KEY="$(security find-generic-password -s gestionale-asta-field-key -w)"
export ASTA_INDEX_KEY="$(security find-generic-password -s gestionale-asta-index-key -w)"
STAMP=$(date +%Y-%m-%d_%H%M)

# 1) Excel protetto da password
./venv/bin/python manage.py backup_excel --dir backup/excel --tieni 120

# 2) Copia del database (formato PostgreSQL), cifrata con la stessa password
pg_dump -Fc "${POSTGRES_DB:-asta}" | openssl enc -aes-256-cbc -pbkdf2 -pass env:ASTA_BACKUP_PASSWORD \
  > "backup/db/asta_${STAMP}.dump.enc"
find backup/db -name '*.dump.enc' -mtime +60 -delete

# 3) Copie esterne (se montate)
for DEST in /Volumes/NAS/backup-asta "$HOME/Library/CloudStorage/OneDrive/backup-asta"; do
  [ -d "$DEST" ] && rsync -a backup/ "$DEST/" || echo "ATTENZIONE: $DEST non raggiungibile"
done
echo "$(date) backup OK"
