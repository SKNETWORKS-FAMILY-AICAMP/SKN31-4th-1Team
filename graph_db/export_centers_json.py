# -*- coding: utf-8 -*-
"""치매안심센터 지도용 JSON 내보내기 스크립트

프론트엔드의 '치매 센터 찾기'(/center-search) 지도 페이지가 사용할 정적 JSON을
data/processed/ 의 CSV 3종에서 생성한다.

왜 Neo4j에 붙지 않는가:
    data/processed/ 의 CSV가 Neo4j AuraDB에 적재된 내용과 동일하다
    (load_to_aura.py 가 이 CSV를 그대로 MERGE한다). CSV를 직접 읽으면 DB 접속 정보
    없이 누구나 재실행할 수 있고, 센터 데이터는 연 1~2회 갱신되는 공공데이터라
    실시간 조회가 필요하지 않다.

왜 pandas를 쓰지 않는가:
    이 스크립트는 표준 라이브러리(csv, json)만으로 동작한다. 프론트 담당자가
    백엔드 가상환경 없이도 돌릴 수 있게 하기 위한 의도적 선택이다.

읽는 파일 (data/processed/):
    nodes_치매안심센터.csv           센터 본문 (313행)
    nodes_프로그램.csv               프로그램ID → 이름 (683행)
    rels_센터_PROVIDES_프로그램.csv   센터ID ↔ 프로그램ID (1,309행)

실행 방법:
    python graph_db/export_centers_json.py
        → ../dementia_front/public/data/centers.json 생성

    출력 경로를 바꾸려면:
    python graph_db/export_centers_json.py --out 경로/centers.json

    실행 후 콘솔 요약(총 센터 수 / 좌표 결측 / 프로그램 보유 센터 수 / 태그별 센터 수)을
    확인할 것. 숫자가 mds/center_map_guide.md 2절과 다르면 원본 데이터가 바뀐 것이다.

주의:
    위도/경도는 위치 정밀도이므로 반올림·정수화하지 않는다. float 원본을 그대로 쓴다.
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE_DIR, 'data', 'processed')

# 기본 출력 경로: 백엔드 repo와 나란히 있는 프론트 repo의 public/data/
DEFAULT_OUT = os.path.normpath(
    os.path.join(BASE_DIR, '..', '..', 'dementia_front', 'public', 'data', 'centers.json')
)

SOURCE_LABEL = '국립중앙의료원 치매안심센터 정보 / 전국치매센터표준데이터'

# ---------------------------------------------------------------------------
# 프로그램 태그 분류 규칙
#
# 원본 nodes_프로그램.csv 의 category 컬럼은 683개 전부 비어 있다(미분류).
# 그래서 프로그램 '이름'의 키워드로 태그를 만든다. 아래 버킷은 실제 데이터에서
# 등장 빈도가 높은 프로그램명을 근거로 정의했다.
#
# 순서가 의미를 가진다: 위에서부터 검사하며, 'support'의 '지원'처럼 범위가 넓은
# 키워드는 마지막에 둔다.
#
# 매칭 전에 프로그램명의 공백을 모두 제거한다. 원본에 '치매조기검진'과
# '치매 조기검진', '맞춤형사례관리'와 '맞춤형 사례관리'처럼 띄어쓰기만 다른
# 중복이 실제로 존재하므로, 공백을 남기면 절반이 누락된다.
# ---------------------------------------------------------------------------
TAG_RULES = [
    ('screening',  ['검진', '선별검사', '치매검사']),
    ('prevention', ['예방', '교육', '인식개선']),
    ('cognitive',  ['인지', '기억']),
    ('daycare',    ['쉼터', '안심마을', '주야간']),
    ('family',     ['가족', '상담', '사례관리', '등록관리', '통합관리']),
    ('support',    ['치료관리비', '치료비', '조호물품', '지원']),
]

TAG_LABELS = {
    'screening': '조기검진',
    'prevention': '예방·교육',
    'cognitive': '인지훈련',
    'daycare': '쉼터·돌봄',
    'family': '가족·상담',
    'support': '비용·물품 지원',
}


def read_csv(filename):
    """data/processed 의 CSV를 dict 리스트로 읽는다.

    CSV에 BOM이 있으므로 utf-8-sig 로 읽는다 (load_to_aura.py와 동일).
    """
    path = os.path.join(PROCESSED_DIR, filename)
    if not os.path.exists(path):
        sys.exit(f'[오류] 파일을 찾을 수 없습니다: {path}')
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def tags_for(program_names):
    """프로그램 이름 목록에서 태그 집합을 만든다."""
    tags = []
    for tag, keywords in TAG_RULES:
        for name in program_names:
            squashed = ''.join(name.split())  # 모든 공백 제거
            if any(kw in squashed for kw in keywords):
                tags.append(tag)
                break
    return tags


def clean(value):
    """빈 문자열을 None으로 정규화한다. 프론트에서 falsy 분기를 단순하게 하기 위함."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def to_float(value, center_id, field):
    """좌표 문자열을 float으로 변환한다. 반올림하지 않는다."""
    try:
        return float(value)
    except (TypeError, ValueError):
        print(f'  [경고] {center_id}: {field} 값을 읽을 수 없습니다 (원본: {value!r})')
        return None


def main():
    parser = argparse.ArgumentParser(description='지도용 centers.json 생성')
    parser.add_argument('--out', default=DEFAULT_OUT, help='출력 JSON 경로')
    args = parser.parse_args()

    centers_raw = read_csv('nodes_치매안심센터.csv')
    programs_raw = read_csv('nodes_프로그램.csv')
    rels_raw = read_csv('rels_센터_PROVIDES_프로그램.csv')

    # 프로그램ID → 이름
    program_name = {}
    for row in programs_raw:
        name = clean(row.get('name'))
        if name:
            program_name[row['프로그램ID']] = name

    # 센터ID → 프로그램 이름 목록 (중복 제거, 원본 순서 유지)
    center_programs = defaultdict(list)
    missing_program_refs = 0
    for row in rels_raw:
        cid = row['센터ID']
        name = program_name.get(row['프로그램ID'])
        if name is None:
            missing_program_refs += 1
            continue
        if name not in center_programs[cid]:
            center_programs[cid].append(name)

    centers = []
    no_coord = 0
    tag_counter = Counter()

    for row in centers_raw:
        cid = row['센터ID']
        lat = to_float(row.get('위도'), cid, '위도')
        lng = to_float(row.get('경도'), cid, '경도')
        if lat is None or lng is None:
            no_coord += 1

        programs = center_programs.get(cid, [])
        tags = tags_for(programs)
        tag_counter.update(tags)

        centers.append({
            'id': cid,
            'name': clean(row.get('name')),
            'type': clean(row.get('유형')),
            'sido': clean(row.get('시도')),
            'sigungu': clean(row.get('시군구')),
            'addr': clean(row.get('주소')),
            'lat': lat,
            'lng': lng,
            'tel': clean(row.get('전화번호')),
            'homepage': clean(row.get('홈페이지')),
            'programs': programs,
            'tags': tags,
        })

    payload = {
        'updated_at': date.today().isoformat(),
        'source': SOURCE_LABEL,
        'tag_labels': TAG_LABELS,
        'centers': centers,
    }

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

    # ---------------- 요약 출력 ----------------
    size_kb = os.path.getsize(out_path) / 1024
    with_programs = sum(1 for c in centers if c['programs'])
    type_counter = Counter(c['type'] for c in centers)

    print(f'\n생성 완료: {out_path}  ({size_kb:.1f} KB)')
    print(f'  총 센터 수        : {len(centers)}')
    print(f'  좌표 결측         : {no_coord}')
    print(f'  프로그램 보유 센터 : {with_programs}  (미보유 {len(centers) - with_programs})')
    print('  유형별            : ' + ', '.join(f'{k} {v}' for k, v in type_counter.most_common()))
    print('  태그별 센터 수     :')
    for tag, _ in TAG_RULES:
        print(f'    - {TAG_LABELS[tag]:<12} {tag_counter[tag]:>4}')
    if missing_program_refs:
        print(f'  [경고] 이름을 찾을 수 없는 프로그램 참조 {missing_program_refs}건 무시됨')
    print()


if __name__ == '__main__':
    main()
