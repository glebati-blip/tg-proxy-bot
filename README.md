# Telegram Proxy Bot

Автоматический бот для сбора и рассылки лучших MTProto прокси с системой администрирования и умным кэшированием.

Основан на [Telegram Proxy Collector](https://github.com/kort0881/telegram-proxy-collector) с расширенной функциональностью.
##  Возможности

-  Автоматический сбор прокси из 4+ источников
-  Проверка скорости и работоспособности
-  Выбор лучшего прокси с учетом региона (RU/EU)
-  Кэширование с проверкой актуальности (24 часа)
-  Панель администратора с управлением пользователями
-  Настраиваемые интервалы рассылки (5мин - 24ч)
-  Детальная статистика работы

##  Быстрый старт

1. Клонируйте репозиторий:
```bash
git clone https://github.com/glebati-blip/tg-proxy-bot
cd telegram-proxy-bot
```
Установите зависимости:

```bash
pip install requests python-telegram-bot schedule python-dotenv
```
Создайте файл .env:
```
env
BOT_TOKEN=ваш_токен_от_BotFather
CHAT_ID=ваш_id_от_userinfobot
```
Запустите бота:

```bash
python main.py
```
 Команды бота
Для всех пользователей:
```
/start - Начало работы и запрос доступа

/proxy - Получить лучший прокси сейчас

/settings - Настроить интервал рассылки

/stats - Статистика работы

/cached - Информация о кэшированном прокси
```
Для администратора:
```
/admin - Панель управления
```
 Технологии
Python 3.9+
python-telegram-bot
SQLite
Schedule
python-dotenv
