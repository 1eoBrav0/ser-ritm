# Сертификат Supabase

Публичный корневой сертификат из раздела Database Settings → SSL configuration.
Он проверяет сертификат и имя сервера при подключении к PostgreSQL; приватных ключей здесь нет.

Источник: https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt

Для Supabase добавь к DATABASE_URL параметр `sslrootcert=certs/supabase-ca-2021.crt`.
Относительный путь считается от корня проекта. Без параметра используется системное хранилище сертификатов.
`sslmode=verify-full` задаётся приложением и не отключается строкой подключения.

Проверено 19 октября 2026. SHA-256 файла:
`700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`.
