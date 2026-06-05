from __future__ import annotations

import argparse
import asyncio

from db_manager import DatabaseManager
from scraper.titan_scraper import TitanScraper


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Titan007 抓取测试")
    parser.add_argument("--db-path", default="football_data.sqlite3", help="数据库文件路径")
    parser.add_argument("--user-data-dir", default="./playwright_profile", help="浏览器用户数据目录")
    parser.add_argument("--headless", action="store_true", default=False, help="无头模式运行")
    parser.add_argument("--match-id", help="详情页 id；如果不传，就从列表页抓取")
    parser.add_argument("--min-matches", type=int, default=10, help="最近场次数最低要求")
    parser.add_argument("--list-url", default="https://bf.titan007.com/football/Over_20260602.htm", help="列表页地址")
    return parser


async def main_async(args: argparse.Namespace) -> None:
    db = DatabaseManager(args.db_path)
    async with TitanScraper(db=db, user_data_dir=args.user_data_dir, headless=args.headless) as scraper:
        if args.match_id:
            rows = [{"match_id": args.match_id}]
            print(f"使用用户传入的详情页id：{args.match_id}")
        else:
            rows = await scraper.fetch_list_page(args.list_url)
            print(f"列表页比赛数量：{len(rows)}")
            if rows:
                print("首个比赛id：", rows[0].get("match_id"))

        if not rows:
            print("没有抓到任何比赛数据")
            return

        total = len(rows)
        qualified = 0
        skipped = 0
        for idx, row in enumerate(rows, start=1):
            match_id = row["match_id"]
            print(f"\n[{idx}/{total}] 正在查询 {match_id} ...")
            detail = await scraper.get_qualified_recent_stats(match_id, min_matches=args.min_matches)
            if not detail:
                skipped += 1
                print(f"[{idx}/{total}] {match_id} 未满足最近场次数要求（已跳过 {skipped} 条，已入库 {qualified} 条）")
                continue

            qualified += 1
            home_recent = scraper.format_recent_summary(detail["home_recent"])
            away_recent = scraper.format_recent_summary(detail["away_recent"])
            print(f"[{idx}/{total}] {match_id} 主队 {detail.get('home_team')}：{home_recent}")
            print(f"[{idx}/{total}] {match_id} 客队 {detail.get('away_team')}：{away_recent}")
            await scraper.sync_match(match_id, detail=detail)
            print(f"[{idx}/{total}] 已保存 {match_id}（已入库 {qualified} 条，已跳过 {skipped} 条）")

        print(f"\n===== 全部完成 =====")
        print(f"总计 {total} 条，已入库 {qualified} 条，已跳过 {skipped} 条")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
