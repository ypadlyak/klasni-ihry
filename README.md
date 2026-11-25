# Класні ігри

Безкоштовні навчальні ігри для уроків інформатики, 0–7 клас.

**Сайт:** https://ypadlyak.github.io/klasni-ihry/ · **English:** https://ypadlyak.github.io/klasni-ihry/en/

| Гра | Клас | Що вчить |
|---|---|---|
| Будинок у перспективі | 7 | лінійна перспектива з двома точками сходу, графічний редактор |
| Пейзаж у перспективі | 7 | одна точка сходу, шари (на задній / передній план) |
| Реферат до друку | 5 | властивості сторінки, форматування, колонтитули, друк |
| Кулькосвіт | 0 | спільна гра всім класом на планшетах, без читання |

## Як грати

**Один учень:** відкрийте гру на сайті. Нічого не треба встановлювати, усе зберігається лише в браузері.

**Увесь клас (рейтинг, таймер, проєктор):** потрібен лише Python 3 на вчительському ноутбуці.

```sh
cd klas
python3 landscape-server.py        # або house-server.py, format-server.py, kulky-server.py
```

Учні в тій самій мережі відкривають адресу, яку покаже сервер. На проєкторі відкрийте `http://localhost:8000/board`.
Параметри: інший порт `python3 landscape-server.py 8080`, почати рейтинг з нуля `--reset`.

Сервери створюють поруч файли рейтингу (`*-scores.json`, `kulky-world.json`) і сертифікат (`.house-cert/`). У них можуть бути імена учнів, тож не викладайте їх у мережу.

## Ліцензія

© 2026 Юрій Падляк (Yuriy Padlyak).

Ігри поширюються за ліцензією [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.uk): їх можна використовувати й змінювати некомерційно, обов'язково зазначаючи автора і поширюючи зміни на тих самих умовах. **Продаж і будь-яке комерційне використання дозволені лише за окремою ліцензією автора.** Пояснення простими словами у [LICENSING.md](LICENSING.md), повний текст у [LICENSE](LICENSE), сторонні компоненти у [THIRD_PARTY.md](THIRD_PARTY.md).

---

## English

Free educational games for computer science lessons (grades K, 5 and 7), in English and Ukrainian: https://ypadlyak.github.io/klasni-ihry/en/

- **A House in Perspective** (grade 7): two-point linear perspective.
- **Landscape in Perspective** (grade 7): one-point perspective and drawing layers.
- **Ready to Print** (grade 5): page setup, margins, fonts, line spacing, headers and footers, printing.
- **Kulkosvit** (kindergarten): one shared block world for the whole class on tablets, no reading needed.

Play solo in the browser, or run `python3 <game>-server.py` from the `klas` folder on the teacher’s laptop for a class leaderboard on the projector (the class kit UI is Ukrainian for now).

© 2026 Yuriy Padlyak, licensed under CC BY-NC-SA 4.0. Commercial use requires a separate license from the author. See [LICENSING.md](LICENSING.md).
