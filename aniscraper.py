import asyncio
import json
import re
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from curl_cffi.requests import AsyncSession

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
        # Impersonate Chrome 120 (mais recente) para bater com os headers
        self.session = AsyncSession(impersonate="chrome120")
        self.semaphore = asyncio.Semaphore(3) # Reduzido drasticamente: Velocidade alta = Bloqueio Cloudflare

 

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
        print("📥 Acessando lista de animes via Bypass...")
        
        # Headers que mimetizam um navegador real acessando o site pela primeira vez
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "referer": "https://www.google.com/", # Simula que você veio do Google
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "cross-site",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # Primeiro, "visita" a home para ganhar um cookie de sessão
        try:
            print("🍪 Pegando cookies de sessão...")
            await self.session.get(self.base_url, headers=headers, timeout=20)
            await asyncio.sleep(2) # Pausa dramática para parecer humano
        except: pass

        # Agora tenta a lista de animes
        url_lista = f"{self.base_url}/lista-de-animes"
        try:
            resp = await self.session.get(url_lista, headers=headers, timeout=30)
            print(f"📡 Resposta da lista: {resp.status_code}")
            
            # Se a Cloudflare bloquear com 403, tentamos o sitemap como fallback
            if resp.status_code != 200:
                print("⚠️ Bloqueado na lista. Tentando Sitemap...")
                resp = await self.session.get(f"{self.base_url}/sitemap.xml", headers=headers, timeout=30)

            links_encontrados = re.findall(r'https?://animefire\.io/animes/[a-z0-9-]+', resp.text)
            
            if links_encontrados:
                links = [f"{l}-todos-os-episodios" if "-todos-os-episodios" not in l else l for l in links_encontrados]
                links = sorted(list(set(links)))
                print(f"🔗 Sucesso! {len(links)} links encontrados.")
                return links
            else:
                print("❌ Nenhum link encontrado. Cloudflare deve ter entregue um desafio de JS.")
                # Debug: salva o que recebeu para você ver no log do GitHub
                with open("debug_resp.html", "w") as f: f.write(resp.text[:2000])
        except Exception as e:
            print(f"❌ Erro na conexão: {e}")
        
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
        links = await self.get_all_links_from_sitemap()
        if not links:
            print("🛑 Falha crítica. O GitHub Actions está sendo bloqueado pela Cloudflare.")
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
