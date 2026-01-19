import asyncio
import os
from aniscraper import AnimeFireAPI
from separador import organizar_database

async def main():
    # Caminho do arquivo principal
    db_path = 'animes_full_db.json'
    
    print("--- Passo 1: Iniciando Scraping Incremental ---")
    scraper = AnimeFireAPI()
    
    # O run_automation já lida com a leitura/escrita do animes_full_db.json
    await scraper.run_automation()

    if os.path.exists(db_path):
        print("--- Passo 2: Organizando Database em categorias ---")
        organizar_database(db_path)
        print("--- Processo Finalizado com Sucesso ---")
    else:
        print("--- Erro: Database não encontrada para separação ---")

if __name__ == "__main__":
    asyncio.run(main())