import asyncio
import json
import re
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

@dataclass
class Video:
    quality: str
    url: str

@dataclass
class Episode:
    number: str
    url: str
    thumb: str = "" 
    videos: List[Video] = field(default_factory=list)

@dataclass
class AnimeData:
    title: str
    slug: str
    url: str
    thumbnail: str 
    description: str
    genres: List[str]
    status: str
    year: str
    episodes: List[Episode] = field(default_factory=list)

class AnimeFireAPI:
    def __init__(self):
        self.base_url = "https://animefire.io"
        self.scraperant_key = "75d0bdb8eb7e4c7e86497459c678f4ea" # COLOQUE SUA CHAVE AQUI
        self.session = AsyncSession(impersonate="chrome120")
        self.semaphore = asyncio.Semaphore(10) 

    async def _get_with_bypass(self, target_url: str):
        """Usa ScrapingAnt (Corrigido) para vencer o 403."""
        if not self.scraperant_key:
            print("❌ ERRO: Chave SCRAPERANT_API_KEY não encontrada!")
            return None

        # 1. Codificar a URL (Transforma :// em %3A%2F%2F)
        import urllib.parse
        encoded_target = urllib.parse.quote(target_url, safe='')
        
        # 2. URL Corrigida (scrapingant com 'ing')
        proxy_url = (
            f"https://api.scrapingant.com/v2/general"
            f"?url={encoded_target}"
            f"&x-api-key={self.scraperant_key}"
            f"&browser=true"
        )

        print(f"🛡️ Enviando requisição para ScrapingAnt (Bypass)...")
        try:
            # ScrapingAnt com browser=true pode demorar, mantemos timeout alto
            resp = await self.session.get(proxy_url, timeout=60)
            return resp
        except Exception as e:
            print(f"❌ Erro de conexão com ScrapingAnt: {e}")
            return None

    async def get_all_links_intelligently(self, db_exists: bool) -> List[str]:
        """
        Se DB existe: Pega só a Home (Lançamentos recentes).
        Se DB não existe: Pega o Sitemap (Carga inicial).
        """
        if db_exists:
            print("fast-forward ⏩ DB encontrada. Verificando apenas novidades na Home...")
            target = self.base_url
        else:
            print("Full-scan 🌎 DB vazia. Acessando Sitemap completo (isso pode demorar)...")
            target = f"{self.base_url}/sitemap.xml"

        try:
            resp = await self._get_with_bypass(target)
            if resp.status_code == 200:
                # Regex captura slugs de animes
                links = re.findall(r'https?://animefire\.io/animes/[a-z0-9-]+', resp.text)
                if links:
                    final = [f"{l}-todos-os-episodios" if "-todos-os-episodios" not in l else l for l in links]
                    return sorted(list(set(final)))
            print(f"⚠️ Falha ao obter links: Status {resp.status_code}")
        except Exception as e:
            print(f"❌ Erro no fetch inteligente: {e}")
        return []

    def _get_info_text(self, soup, label):
        for info in soup.select(".animeInfo"):
            b_tag = info.select_one("b")
            if b_tag and label.lower() in b_tag.text.lower():
                span = info.select_one("span")
                return span.text.strip() if span else "N/A"
        return "N/A"

    def _clean_slug_for_assets(self, slug: str) -> str:
        return slug.replace("-dublado", "")

    async def get_all_links_from_sitemap(self) -> List[str]:
        print("📥 Acessando fontes com Cookie Injetado...")
        # Tentaremos primeiro o sitemap com esse cookie
        urls = [f"{self.base_url}/sitemap.xml", f"{self.base_url}/lista-de-animes"]
        
        for url in urls:
            try:
                print(f"📡 Tentando: {url}")
                resp = await self.session.get(url, timeout=30)
                
                # Se o cookie funcionar, o status será 200
                if resp.status_code == 200:
                    links = re.findall(r'https?://animefire\.io/animes/[a-z0-9-]+', resp.text)
                    if links:
                        # Limpeza e formatação
                        final = [f"{l}-todos-os-episodios" if "-todos-os-episodios" not in l else l for l in links]
                        final = sorted(list(set(final)))
                        print(f"🔗 Sucesso! {len(final)} links extraídos.")
                        return final
                else:
                    print(f"⚠️ Erro {resp.status_code} em {url}")
                    # Se recebermos 403 aqui, o 'sid' pode ter expirado ou o IP do GitHub foi marcado
            except Exception as e:
                print(f"❌ Falha: {e}")
        return []

    async def get_video_links(self, ep_name: str, ep_url: str, anime_slug: str) -> Episode:
        async with self.semaphore:
            try:
                ep_number = ep_url.split("/")[-1]
                asset_slug = self._clean_slug_for_assets(anime_slug)
                ep_thumb = f"https://animefire.io/img/video/{asset_slug}/{ep_number}.webp"
                resp = await self.session.get(ep_url, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                videos = []

                video_tag = soup.select_one("video#my-video")
                if video_tag and video_tag.get("data-video-src"):
                    api_resp = await self.session.get(video_tag["data-video-src"])
                    for item in api_resp.json().get("data", []):
                        if "source=blogger" not in item.get("src", ""):
                            videos.append(Video(quality=item.get("label"), url=item.get("src")))

                if not videos:
                    iframe = soup.select_one("div#div_video iframe")
                    if iframe:
                        iframe_resp = await self.session.get(iframe.get("src"))
                        m = re.search(r'play_url"\s*:\s*"([^"]+)', iframe_resp.text)
                        if m and "source=blogger" not in m.group(1):
                            videos.append(Video(quality="SD/HD", url=m.group(1)))
                
                return Episode(number=ep_name, url=ep_url, thumb=ep_thumb, videos=videos)
            except Exception:
                return Episode(number=ep_name, url=ep_url, thumb="", videos=[])

    async def scrape_full_anime(self, url: str) -> Optional[AnimeData]:
        async with self.semaphore:
            try:
                resp = await self.session.get(url, timeout=20)
                soup = BeautifulSoup(resp.text, "html.parser")
                title_tag = soup.find("h1")
                if not title_tag: return None
                
                anime_slug = url.split("/")[-1].replace("-todos-os-episodios", "")
                asset_slug = self._clean_slug_for_assets(anime_slug)
                
                anime = AnimeData(
                    title=title_tag.text.strip(),
                    slug=anime_slug,
                    url=url,
                    thumbnail=f"https://animefire.io/img/animes/{asset_slug}-large.webp",
                    description=soup.select_one("div.divSinopse").text.strip() if soup.select_one("div.divSinopse") else "",
                    genres=[a.text.strip() for a in soup.select("a.spanGeneros")],
                    status=self._get_info_text(soup, "Status"),
                    year=self._get_info_text(soup, "Ano")
                )

                ep_elements = soup.select("div.div_video_list > a")
                if ep_elements:
                    tasks = [self.get_video_links(a.text.strip(), a["href"], anime_slug) for a in ep_elements]
                    res = await asyncio.gather(*tasks)
                    anime.episodes = [e for e in res if e.videos]

                return anime
            except Exception:
                return None

    async def run_automation(self):
        db_path = "animes_full_db.json"
        db_exists = os.path.exists(db_path) and os.path.getsize(db_path) > 10
        
        # 1. Busca links de forma inteligente
        links = await self.get_all_links_intelligently(db_exists)
        
        if not links:
            print("🛑 Nenhum link novo para processar.")
            return

        db_map = {}
        if db_exists:
            with open(db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                db_map = {a['slug']: a for a in data}

        # 2. Filtra o que realmente precisa de scrap
        # Processa se: Não estiver na DB OU se o status for "Em lançamento"
        links_to_process = [
            l for l in links 
            if l.split("/")[-1].replace("-todos-os-episodios", "") not in db_map 
            or db_map[l.split("/")[-1].replace("-todos-os-episodios", "")].get("status") == "Em lançamento"
        ]

        if not links_to_process:
            print("✅ Nada novo sob o sol. Tudo atualizado.")
            return

        print(f"🚀 Processando {len(links_to_process)} animes...")
        batch_size = 5
        for i in range(0, len(links_to_process), batch_size):
            batch = links_to_process[i:i+batch_size]
            results = await asyncio.gather(*[self.scrape_full_anime(l) for l in batch])
            
            for r in results:
                if r: db_map[r.slug] = asdict(r)
            
            # Salva parcial para não perder progresso
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(list(db_map.values()), f, indent=4, ensure_ascii=False)
            
            print(f"⏳ Progresso: {len(db_map)} animes no total.")
            await asyncio.sleep(1)
