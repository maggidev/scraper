import asyncio
import os
import json
from aniscraper import AnimeFireAPI
from separador import organizar_database
from limpar import processar_lista_animes # Certifique-se de que o limpar.py tenha essa função

async def main():
    db_path = 'animes_full_db.json'
    db_limpa_path = 'animes_limpos.json'
    
    print("--- Passo 1: Iniciando Scraping Incremental ---")
    # Se o arquivo não existir ou estiver vazio [], o run_automation criará um novo
    scraper = AnimeFireAPI()
    await scraper.run_automation()

    # Validação: O scraper funcionou e gerou dados?
    if os.path.exists(db_path) and os.path.getsize(db_path) > 2: # > 2 bytes para ignorar []
        
        print("--- Passo 2: Limpando a Database (Removendo Blogger e Links Vazios) ---")
        # Chamamos a função do limpar.py para gerar a lista limpa
        # Note: Ajustei os nomes para manter a consistência
        processar_lista_animes(db_path, db_limpa_path)
        
        if os.path.exists(db_limpa_path):
            print("--- Passo 3: Organizando em Categorias e Lançamentos ---")
            # O separador agora trabalha sobre a lista já limpa
            organizar_database(db_limpa_path)
            
            print("--- [OK] Processo Finalizado com Sucesso ---")
        else:
            print("--- [ERRO] Falha ao gerar database limpa ---")
            
    else:
        print("--- [AVISO] Database principal está vazia. Nada para processar. ---")

if __name__ == "__main__":
    asyncio.run(main())
