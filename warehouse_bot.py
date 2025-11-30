import discord
from discord.ext import commands, tasks
from google.oauth2.service_account import Credentials
import gspread
import json
import os
from datetime import datetime
import re
import hashlib

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

SERVER_ID = int(os.getenv("GUILD_ID", "1397286059406000249"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "1400374068997521438"))
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SHEET_NAME = "Dropy2"

# Soubor pro uložení stavu (persistent storage)
STATE_FILE = "/tmp/warehouse_bot_state.json"

# Globální proměnné
last_row_hashes = {}
first_check_done = False

def load_state():
    """Načti poslední známý stav ze souboru"""
    global last_row_hashes
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                last_row_hashes = data.get('last_row_hashes', {})
                print(f"✅ Načten poslední stav: {len(last_row_hashes)} řádků")
        else:
            last_row_hashes = {}
            print("📝 Žádný předchozí stav nenalezen")
    except Exception as e:
        print(f"⚠️  Chyba při načítání stavu: {e}")
        last_row_hashes = {}

def save_state():
    """Ulož aktuální stav do souboru"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({'last_row_hashes': last_row_hashes}, f)
        print(f"💾 Stav uložen: {len(last_row_hashes)} řádků")
    except Exception as e:
        print(f"❌ Chyba při ukládání stavu: {e}")

def create_row_hash(row_data):
    """Vytvoř unikátní hash pro řádek (item|popis)"""
    row_str = f"{row_data['item']}|{row_data['popis']}"
    return hashlib.md5(row_str.encode()).hexdigest()

print("="*60)
print("WAREHOUSE BOT - CZM8")
print("="*60)
print(f"SHEET_ID: {SHEET_ID}")
print(f"SHEET_NAME: {SHEET_NAME}")

def get_sheets_client():
    try:
        creds_json = os.getenv("GOOGLE_CREDENTIALS")
        if not creds_json:
            print("❌ GOOGLE_CREDENTIALS not found!")
            return None
            
        creds_dict = json.loads(creds_json)
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        print("✅ Google Sheets client OK")
        return client
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def is_valid_row(item, popis):
    """Zkontroluj jestli je řádek validní"""
    invalid_keywords = ['nic', 'item', 'popis', 'celkem', '']
    
    item_lower = str(item).lower().strip()
    popis_lower = str(popis).lower().strip()
    
    if not item or not popis:
        return False
    
    if item_lower in invalid_keywords or popis_lower in invalid_keywords:
        return False
    
    return True

def get_warehouse_data():
    try:
        client = get_sheets_client()
        if not client:
            return None
        
        print(f"Opening sheet {SHEET_ID}...")
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        print("✅ Sheet opened")
        
        # Čti sloupce B, C - řádky 2-1000
        # B = Item, C = Popis
        all_cells = sheet.range('B2:C1000')
        print(f"✅ Got {len(all_cells)} cells")
        
        if len(all_cells) >= 2:
            data = []
            for i in range(0, len(all_cells), 2):  # 2 sloupce (B-C)
                row_data = all_cells[i:i+2]
                
                if len(row_data) >= 1 and row_data[0].value:
                    item = str(row_data[0].value).strip()
                    popis = str(row_data[1].value).strip() if len(row_data) > 1 else ""
                    
                    # VALIDACE
                    if is_valid_row(item, popis):
                        data.append({
                            "item": item,
                            "popis": popis
                        })
            
            print(f"✅ Got {len(data)} rows of data")
            return data if data else None
        else:
            return None
    except Exception as e:
        print(f"❌ Error reading sheets: {e}")
        import traceback
        traceback.print_exc()
        return None

def create_embed(title, description, color, timestamp):
    """Vytvoří embed"""
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=timestamp
    )

async def send_new_warehouse_item(channel, item):
    """Pošli novou položku a vrať ID zprávy"""
    try:
        embed = create_embed(
            "📦 Nová Položka",
            "",
            discord.Color.from_rgb(52, 211, 153),
            datetime.now()
        )
        
        embed.add_field(
            name="💳 ",
            value=(f"Item: {item['item']}\n"
                   f"Popis: {item['popis']}"),
            inline=False
        )
        
        msg = await channel.send(embed=embed)
        print(f"✅ Nová položka poslána: {item['item']} (ID: {msg.id})")
        return msg.id
    except Exception as e:
        print(f"❌ Chyba při posílání položky: {e}")
        import traceback
        traceback.print_exc()
        return None

@tasks.loop(minutes=2)
async def check_new_warehouse_items():
    """Kontroluj nové skladové položky a změny"""
    global last_row_hashes, first_check_done
    
    print("\n🔍 Kontrola skladových položek...")
    data = get_warehouse_data()
    
    if not data:
        print("❌ Nelze přečíst data")
        return
    
    try:
        guild = bot.get_guild(SERVER_ID)
        if not guild:
            print(f"❌ Server {SERVER_ID} nenalezen!")
            return
        
        channel = guild.get_channel(CHANNEL_ID)
        if not channel:
            print(f"❌ Kanál {CHANNEL_ID} nenalezen!")
            return
        
        # PRVNÍ KONTROLA - jen si zapamatuj všechny řádky
        if not first_check_done:
            print(f"📌 PRVNÍ KONTROLA - Zapamatuji si {len(data)} stávajících položek")
            
            for item in data:
                row_hash = create_row_hash(item)
                last_row_hashes[row_hash] = {
                    'data': item,
                    'message_id': None
                }
            
            save_state()
            first_check_done = True
            print(f"⏭️  Příští nové položky budou poslány jako notifikace")
            return
        
        # DALŠÍ KONTROLY - Detekuj nové a upravené řádky
        current_hashes = set()
        new_items = []
        
        for item in data:
            row_hash = create_row_hash(item)
            current_hashes.add(row_hash)
            
            if row_hash not in last_row_hashes:
                # NOVÝ ŘÁDEK
                print(f"📈 Nový řádek: {item['item']}")
                new_items.append(item)
                last_row_hashes[row_hash] = {
                    'data': item,
                    'message_id': None
                }
        
        # Pošli nové položky
        for item in new_items:
            row_hash = create_row_hash(item)
            msg_id = await send_new_warehouse_item(channel, item)
            if msg_id:
                last_row_hashes[row_hash]['message_id'] = msg_id
        
        # Detekuj SMAZANÉ řádky
        deleted_hashes = set(last_row_hashes.keys()) - current_hashes
        if deleted_hashes:
            print(f"🗑️  Smazáno {len(deleted_hashes)} řádků")
            for deleted_hash in deleted_hashes:
                del last_row_hashes[deleted_hash]
        
        if not new_items and not deleted_hashes:
            print("✅ Žádné změny")
        
        save_state()
        
    except Exception as e:
        print(f"❌ Chyba v kontrole: {e}")
        import traceback
        traceback.print_exc()

@check_new_warehouse_items.before_loop
async def before_check():
    """Čekej než je bot připraven"""
    await bot.wait_until_ready()

@bot.command(name="warehouse")
async def warehouse_command(ctx):
    """Zobrazí všechny skladové položky"""
    print("Command: !warehouse")
    data = get_warehouse_data()
    if data:
        # Hlavní embed s počtem
        main_embed = create_embed(
            "📦 Sklad CZM8",
            "Přehled všech položek",
            discord.Color.gold(),
            datetime.now()
        )
        
        main_embed.add_field(
            name="📊 Celkem",
            value=f"`{len(data)} položek`",
            inline=False
        )
        
        await ctx.send(embed=main_embed)
        
        # Pošli položky po 10 na embed
        chunk_size = 10
        total_chunks = (len(data) + chunk_size - 1) // chunk_size
        
        for chunk_idx in range(0, len(data), chunk_size):
            chunk = data[chunk_idx:chunk_idx + chunk_size]
            part_num = (chunk_idx // chunk_size) + 1
            
            color = discord.Color.from_rgb(52, 211, 153) if chunk_idx == 0 else discord.Color.from_rgb(59, 130, 246)
            
            if total_chunks == 1:
                title = "📦 Položky"
            else:
                title = f"📦 Položky ({part_num}. část)"
            
            embed = create_embed(
                title,
                "",
                color,
                datetime.now()
            )
            
            for item in chunk:
                value = (f"Item: {item['item']}\n"
                        f"Popis: {item['popis']}")
                
                embed.add_field(
                    name=f"💳 Položka",
                    value=value,
                    inline=False
                )
            
            await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Nemohu přečíst data z Google Sheets")

@bot.command(name="test")
async def test(ctx):
    """Test bota"""
    embed = discord.Embed(
        title="✅ Bot Funguje",
        description="Warehouse bot je online!",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print("="*60)
    print(f"Bot: {bot.user}")
    print("="*60)
    
    # Načti stav při startu
    load_state()
    
    print("READY")
    print("="*60)
    
    if not check_new_warehouse_items.is_running():
        check_new_warehouse_items.start()
        print("🔍 Kontrola skladových položek spuštěna (každých 2 minuty)")

token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
