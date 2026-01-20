import asyncio
import json
import re
import warnings
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Silencia avisos de parser
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

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
        self.session = AsyncSession(impersonate="chrome110")
        self.session.headers.update({"Referer": self.base_url})
        self.semaphore = asyncio.Semaphore(15) 

    # --- MÉTODO CORRIGIDO (INDENTADO PARA DENTRO DA CLASSE) ---
    async def get_valid_proxy(self):
        print("🔍 Buscando lista de proxies brasileiros...")
        url_proxies = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=br&proxy_format=ipport&http_support=true"
        
        try:
            resp = await self.session.get(url_proxies, timeout=15)
            proxy_list = resp.text.splitlines()
            print(f"✅ {len(proxy_list)} proxies encontrados. Testando...")

            for proxy in proxy_list:
                proxy_url = f"http://{proxy}"
                try:
                    # Testa no próprio site do anime para ver se ele aceita o IP
                    test_resp = await self.session.get(
                        "https://animefire.io", 
                        proxies={"http": proxy_url, "https": proxy_url},
                        timeout=5
                    )
                    if test_resp.status_code == 200:
                        print(f"🚀 Proxy funcional encontrado: {proxy}")
                        return proxy_url
                except:
                    continue
            return None
        except Exception as e:
            print(f"❌ Erro ao obter lista de proxies: {e}")
            return None

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
        """Extrai links diretamente da LISTA HTML (mais difícil de bloquear que o XML)."""
        print("📥 Acessando lista de animes para buscar URLs...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        try:
            # Trocamos o sitemap.xml pela página de lista comum
            resp = await self.session.get(f"{self.base_url}/lista-de-animes", headers=headers, timeout=30)
            
            # Pega todos os links que apontam para animes
            links_brutos = re.findall(r'https?://animefire\.io/animes/[a-z0-9-]+', resp.text)
            
            # Adiciona o sufixo necessário
            links = [f"{l}-todos-os-episodios" if "-todos-os-episodios" not in l else l for l in links_brutos]
            
            final_links = sorted(list(set(links)))
            print(f"🔗 Links encontrados: {len(final_links)}")
            return final_links
        except Exception as e:
            print(f"❌ Falha ao buscar lista: {e}")
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
        # 1. Tenta proxy
        valid_proxy = await self.get_valid_proxy()
        if valid_proxy:
            self.session.proxies = {"http": valid_proxy, "https": valid_proxy}
            print("🛡️ Proxy ativado.")

        # 2. Busca links
        links = await self.get_all_links_from_sitemap()
        
        if not links:
            print("🛑 Falha ao obter links. Abortando.")
            return

        db_path = "animes_full_db.json"
        db_map = {}

        if os.path.exists(db_path):
            try:
                with open(db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    db_map = {a['slug']: a for a in data}
            except:
                pass

        links_to_process = [l for l in links if l.split("/")[-1].replace("-todos-os-episodios", "") not in db_map or db_map[l.split("/")[-1].replace("-todos-os-episodios", "")].get("status") == "Em lançamento"]

        if not links_to_process:
            print("✅ DB Atualizada.")
            return

        print(f"🚀 Processando {len(links_to_process)} itens...")
        batch_size = 10
        for i in range(0, len(links_to_process), batch_size):
            batch = links_to_process[i:i+batch_size]
            results = await asyncio.gather(*[self.scrape_full_anime(l) for l in batch])
            for r in results:
                if r: db_map[r.slug] = asdict(r)
            
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(list(db_map.values()), f, indent=4, ensure_ascii=False)
            print(f"⏳ Salvo: {len(db_map)} animes.")
