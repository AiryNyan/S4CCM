#include "common.h"

#include <cmath>

const QHash<QString, QString> STATUS_LABEL_MAP = {
    {u"active"_s, u"사용 중"_s}, {u"disabled"_s, u"비활성"_s}, {u"quarantine"_s, u"충돌 테스트로 잠시 뺌"_s}};

QString statusLabel(const QString& status) {
    return STATUS_LABEL_MAP.value(status, status);
}

using Theme = QHash<QString, QString>;

static const QHash<QString, Theme>& themes() {
    static const QHash<QString, Theme> t = {
        {u"dark"_s,
         {{"window", "#1b1a18"}, {"sidebar", "#171614"}, {"surface", "#232220"}, {"base", "#1f1e1c"}, {"alt", "#232220"},
          {"input", "#161514"}, {"border", "#302e2b"}, {"border_soft", "#282725"}, {"border_strong", "#4a4743"},
          {"hover", "#2b2a27"}, {"pressed", "#33312e"}, {"button", "#262523"},
          {"text", "#ebe9e4"}, {"muted", "#9b978e"}, {"faint", "#66635d"},
          {"accent", "#5bb974"}, {"accent_hover", "#6cc684"}, {"on_accent", "#0e1a11"}, {"sel", "#26382c"},
          {"danger", "#e0736a"}, {"danger_soft", "#3a2523"}, {"warn", "#e0ac4b"},
          {"scroll", "#3a3835"}, {"scroll_hover", "#4d4a46"}, {"track", "#2b2a27"}}},
        {u"light"_s,
         {{"window", "#f5f4f0"}, {"sidebar", "#eeede8"}, {"surface", "#fbfaf7"}, {"base", "#ffffff"}, {"alt", "#faf9f6"},
          {"input", "#ffffff"}, {"border", "#dfddd6"}, {"border_soft", "#ebe9e3"}, {"border_strong", "#b9b6ad"},
          {"hover", "#ebe9e3"}, {"pressed", "#e1dfd8"}, {"button", "#ffffff"},
          {"text", "#1e1d1a"}, {"muted", "#6c6961"}, {"faint", "#a5a299"},
          {"accent", "#2e8b4f"}, {"accent_hover", "#277a45"}, {"on_accent", "#ffffff"}, {"sel", "#dcefe2"},
          {"danger", "#c2453c"}, {"danger_soft", "#f8e4e1"}, {"warn", "#a46c12"},
          {"scroll", "#cfccc4"}, {"scroll_hover", "#b9b6ad"}, {"track", "#e6e4de"}}},
    };
    return t;
}

static QString g_theme = u"dark"_s;

static const QHash<QString, QString>& groupColors() {
    static const QHash<QString, QString> c = {
        {u"overrides"_s, u"#c49a52"_s}, {u"cas"_s, u"#c97a93"_s}, {u"bb"_s, u"#6fa483"_s}, {u"tuning"_s, u"#7d8fc4"_s},
        {u"animations"_s, u"#9b86bd"_s}, {cats::SCRIPT_MOD, u"#5e9dbc"_s}, {cats::UNSORTED, u"#8f8b82"_s}};
    return c;
}

QString tok(const char* name) {
    return themes()[g_theme].value(QString::fromLatin1(name));
}

static QString fontDir() {
    return QCoreApplication::applicationDirPath() + u"/fonts"_s;
}

QStringList uiFamilies() {
    static QStringList families;
    static bool done = false;
    if (!done) {
        done = true;
        QStringList loaded;
        QDir dir(fontDir());
        if (dir.exists()) {
            QStringList names = dir.entryList(QDir::Files, QDir::NoSort);
            std::sort(names.begin(), names.end());
            for (const QString& name : names) {
                const QString low = name.toLower();
                if (low.endsWith(u".ttf"_s) || low.endsWith(u".otf"_s)) {
                    const int fid = QFontDatabase::addApplicationFont(dir.filePath(name));
                    if (fid >= 0)
                        for (const QString& fam : QFontDatabase::applicationFontFamilies(fid))
                            if (!loaded.contains(fam))
                                loaded << fam;
                }
            }
        }
        for (const QString& f : loaded)
            if (f.toLower().remove(u' ').contains(u"nanum"_s)) {
                families << f;
                break;
            }
        families << u"Segoe UI Variable Text"_s << u"Segoe UI"_s << u"Malgun Gothic"_s;
    }
    return families;
}

bool usesNanum() {
    return uiFamilies().first().toLower().remove(u' ').contains(u"nanum"_s);
}

QString humanSize(qint64 size) {
    double n = double(size);
    for (const char* unit : {"B", "KB", "MB", "GB"}) {
        const bool last = qstrcmp(unit, "GB") == 0;
        if (n < 1024 || last)
            return QString::number(n, 'f', qstrcmp(unit, "B") == 0 ? 0 : 1) + u' ' + QLatin1String(unit);
        n /= 1024;
    }
    return QString::number(n);
}

QString categoryPathLabel(const QString& key) {
    const QString g = cats::groupOf(key);
    const QString glabel = cats::groupLabel(g);
    const QString lbl = cats::label(key);
    if (g == key || g == cats::SCRIPT_MOD || g == cats::UNSORTED || lbl == glabel)
        return lbl;
    return glabel.split(u' ').first() + u" › "_s + lbl;
}

static QColor blend(const QString& fg, const QString& bg, double amount) {
    const QColor a(fg), b(bg);
    // Python 의 round(): 정확히 .5 일 때 짝수로.
    auto r = [amount](int x, int y) { return int(std::nearbyint(x * amount + y * (1 - amount))); };
    return QColor(r(a.red(), b.red()), r(a.green(), b.green()), r(a.blue(), b.blue()));
}

// 썸네일 메모리 캐시. 용량(KB)에 한도를 두고, 넘치면 가장 오래 안 쓴 것부터 버린다.
// (원본 Python 판은 한도 없이 쌓았다. CC 가 수천 개면 수백 MB 가 될 수 있다.)
constexpr int ICON_CACHE_KB = 96 * 1024;     // 목록·중복 검사·트레이의 작은 아이콘
constexpr int PIXMAP_CACHE_KB = 128 * 1024;  // 그리드 썸네일 (크기별)
static QCache<QString, QIcon> g_iconCache(ICON_CACHE_KB);
static QCache<QString, QPixmap> g_pixmapCache(PIXMAP_CACHE_KB);   // "경로\n크기"

static int costKb(const QPixmap& pm) {
    return qMax(1, int(qint64(pm.width()) * pm.height() * 4 / 1024));
}
static QHash<QString, QPixmap> g_placeholderCache;

QPixmap placeholder(const QString& group, int size) {
    const QString key = g_theme + u':' + group + u':' + QString::number(size);
    auto found = g_placeholderCache.constFind(key);
    if (found != g_placeholderCache.constEnd())
        return *found;
    const QString color = groupColors().value(group, tok("border_strong"));
    QPixmap pm(size, size);
    pm.fill(Qt::transparent);
    QPainter p(&pm);
    p.setRenderHint(QPainter::Antialiasing);
    const int m = qMax(2, size / 16);
    p.setBrush(blend(color, tok("surface"), 0.16));
    p.setPen(blend(color, tok("surface"), 0.35));
    p.drawRoundedRect(m, m, size - 2 * m - 1, size - 2 * m - 1, size / 12.0, size / 12.0);
    if (group.isEmpty()) {
    } else if (size >= 48) {
        p.setPen(blend(color, tok("text"), 0.8));
        p.setFont(QFont(uiFamilies(), qMax(7, size / 14), QFont::DemiBold));
        QString text = u"미분류"_s;
        if (group == u"cas"_s) text = u"CAS"_s;
        else if (group == u"bb"_s) text = u"건축/구매"_s;
        else if (group == u"tuning"_s) text = u"게임 모드"_s;
        else if (group == u"animations"_s) text = u"포즈"_s;
        else if (group == cats::SCRIPT_MOD) text = u"스크립트"_s;
        else if (group == u"overrides"_s) text = u"오버레이"_s;
        p.drawText(pm.rect(), Qt::AlignCenter, text);
    } else {
        p.setBrush(QColor(color));
        p.setPen(Qt::NoPen);
        const double d = size / 4.0;
        p.drawEllipse(QRectF(size / 2.0 - d / 2, size / 2.0 - d / 2, d, d));
    }
    p.end();
    g_placeholderCache.insert(key, pm);
    return pm;
}

QIcon itemIcon(const Item& item) {
    const QString& path = item.displayThumb;
    if (!path.isEmpty() && QFileInfo::exists(path)) {
        if (const QIcon* found = g_iconCache.object(path))
            return *found;
        const QPixmap pm(path);
        QIcon ic{pm};
        g_iconCache.insert(path, new QIcon(ic), costKb(pm));
        return ic;
    }
    return QIcon(placeholder(cats::groupOf(item.category)));
}

QPixmap itemPixmap(const Item& item, int size) {
    const QString& path = item.displayThumb;
    if (!path.isEmpty() && QFileInfo::exists(path)) {
        const QString key = path + u'\n' + QString::number(size);
        QPixmap pm;
        if (const QPixmap* found = g_pixmapCache.object(key)) {
            pm = *found;
        } else {
            pm = QPixmap(path);
            if (!pm.isNull()) {
                pm = pm.scaled(size, size, Qt::KeepAspectRatio, Qt::SmoothTransformation);
                g_pixmapCache.insert(key, new QPixmap(pm), costKb(pm));
            }
        }
        if (!pm.isNull())
            return pm;
    }
    return placeholder(cats::groupOf(item.category), size);
}

void forgetIcon(const QString& path) {
    if (path.isEmpty())
        return;
    g_iconCache.remove(path);
    const QString prefix = path + u'\n';
    for (const QString& k : g_pixmapCache.keys())
        if (k.startsWith(prefix))
            g_pixmapCache.remove(k);
}

CategoryCombo::CategoryCombo(QWidget* parent, bool includeScript) : QComboBox(parent) {
    setMaxVisibleItems(25);
    if (includeScript)
        addItem(u"스크립트 모드"_s, cats::SCRIPT_MOD);
    for (const auto& c : cats::CATEGORIES)
        addItem(categoryPathLabel(c.key), c.key);
}

void CategoryCombo::setKey(const QString& key) {
    const int i = findData(key);
    if (i >= 0)
        setCurrentIndex(i);
}

void reveal(const QString& path) {
    if (QFileInfo(path).isDir())
        QDesktopServices::openUrl(QUrl::fromLocalFile(path));
    else if (QFileInfo::exists(path))
        QProcess::startDetached(u"explorer"_s, {u"/select,"_s, QDir::toNativeSeparators(QDir::cleanPath(path))});
}

bool confirm(QWidget* parent, const QString& title, const QString& text) {
    return QMessageBox::question(parent, title, text, QMessageBox::Yes | QMessageBox::No) == QMessageBox::Yes;
}

void repolish(QWidget* w) {
    w->style()->polish(w);
}

static QString writeAssets(const Theme& t) {
    const QString d = core().appDir + u"/ui/"_s + g_theme;
    QDir().mkpath(d);
    auto c = [&t](const char* k) { return t.value(QString::fromLatin1(k)); };
    const QString check = u"<polyline points=\"4,8.5 7,11.5 12.5,5\" fill=\"none\" stroke=\"%1\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>"_s.arg(c("on_accent"));
    const QString rect = u"<rect x=\"1\" y=\"1\" width=\"14\" height=\"14\" rx=\"4\" fill=\"%1\" stroke=\"%2\" stroke-width=\"1.2\"/>"_s;
    const QString circle = u"<circle cx=\"8\" cy=\"8\" r=\"6.5\" fill=\"%1\" stroke=\"%2\" stroke-width=\"1.2\"/>"_s;
    const QString line = u"<polyline points=\"%1\" fill=\"none\" stroke=\"%2\" stroke-width=\"1.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>"_s;
    const QList<std::pair<QString, QString>> svgs = {
        {u"check_off"_s, rect.arg(c("input"), c("border_strong"))},
        {u"check_on"_s, rect.arg(c("accent"), c("accent")) + check},
        {u"check_off_dis"_s, rect.arg(c("hover"), c("border"))},
        {u"check_on_dis"_s, rect.arg(c("faint"), c("faint")) + check},
        {u"radio_off"_s, circle.arg(c("input"), c("border_strong"))},
        {u"radio_on"_s, circle.arg(c("input"), c("accent")) + u"<circle cx=\"8\" cy=\"8\" r=\"3.5\" fill=\"%1\"/>"_s.arg(c("accent"))},
        {u"radio_dis"_s, circle.arg(c("hover"), c("border"))},
        {u"down"_s, line.arg(u"4,6 8,10 12,6"_s, c("muted"))},
        {u"up"_s, line.arg(u"4,10 8,6 12,10"_s, c("muted"))},
        {u"right"_s, line.arg(u"6,4 10,8 6,12"_s, c("muted"))},
        {u"left"_s, line.arg(u"10,4 6,8 10,12"_s, c("muted"))},
        {u"thumb_small"_s, u"<rect x=\"5\" y=\"5\" width=\"6\" height=\"6\" rx=\"1.5\" fill=\"none\" stroke=\"%1\" stroke-width=\"1.4\"/>"_s.arg(c("muted"))},
        {u"thumb_large"_s, u"<rect x=\"2\" y=\"2\" width=\"12\" height=\"12\" rx=\"2.5\" fill=\"none\" stroke=\"%1\" stroke-width=\"1.4\"/>"_s.arg(c("muted"))},
    };
    for (const auto& [name, body] : svgs) {
        QFile f(d + u'/' + name + u".svg"_s);
        if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            f.write((u"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\">"_s + body + u"</svg>"_s).toUtf8());
        }
    }
    return QString(d).replace(u'\\', u'/');
}

// Python 판의 QSS 를 그대로 둔다 ({{ }} 는 중괄호, {이름} 은 테마 색).
static const char* QSS = R"QSS(
* {{ outline: 0; }}
QMainWindow, QDialog, QMessageBox {{ background: {window}; }}
QWidget {{ color: {text}; }}
QLabel#h1 {{ font-size: 15pt; font-weight: bold; }}
QLabel#h2 {{ font-size: 12pt; font-weight: bold; }}
QLabel#percent {{ color: {text}; font-weight: bold; }}
QProgressBar#gauge {{ max-height: 8px; min-height: 8px; border-radius: 4px; }}
QProgressBar#gauge::chunk {{ border-radius: 4px; }}
QLabel#muted {{ color: {muted}; }}
QLabel#warn {{ color: {warn}; }}
QLabel#section {{ color: {muted}; font-weight: bold; }}
QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}

QToolBar {{ background: {window}; border: none; border-bottom: 1px solid {border}; padding: 6px 10px; spacing: 2px; }}
QToolBar::separator {{ width: 1px; background: {border}; margin: 6px 8px; }}
QToolBar QToolButton {{ padding: 6px 12px; border-radius: 6px; border: 1px solid transparent; }}
QToolBar QToolButton:hover {{ background: {hover}; }}
QToolBar QToolButton:pressed {{ background: {pressed}; }}
QToolButton#accent {{ background: {accent}; color: {on_accent}; font-weight: bold; }}
QToolButton#accent:hover {{ background: {accent_hover}; }}

QTreeWidget#sidebar {{ background: {sidebar}; border: none; border-right: 1px solid {border}; padding: 8px 6px; }}
QTreeWidget#sidebar::item {{ height: 30px; border: none; padding-left: 2px; }}
QTreeWidget#sidebar::item:hover {{ background: {hover}; }}
QTreeWidget#sidebar::item:selected {{ background: {sel}; color: {text}; }}

QPushButton {{ background: {button}; border: 1px solid {border}; border-radius: 6px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {hover}; border-color: {border_strong}; }}
QPushButton:pressed {{ background: {pressed}; }}
QPushButton:disabled {{ color: {faint}; background: transparent; border-color: {border_soft}; }}
QPushButton#primary {{ background: {accent}; border-color: {accent}; color: {on_accent}; font-weight: bold; }}
QPushButton#primary:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton#primary:disabled {{ background: {hover}; border-color: {hover}; color: {faint}; }}
QPushButton#danger {{ background: transparent; border-color: {danger}; color: {danger}; }}
QPushButton#danger:hover {{ background: {danger_soft}; }}
QPushButton#danger:disabled {{ border-color: {border_soft}; color: {faint}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {input}; border: 1px solid {border}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {accent}; selection-color: {on_accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QPlainTextEdit:disabled {{ color: {faint}; background: {window}; }}
QLineEdit[readOnly="true"] {{ color: {muted}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({assets}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: 1px solid {border}; border-radius: 6px; padding: 4px;
    selection-background-color: {sel}; selection-color: {text};
}}
QSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 18px; background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url({assets}/up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({assets}/down.svg); width: 10px; height: 10px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QAbstractItemView::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked, QAbstractItemView::indicator:unchecked {{ image: url({assets}/check_off.svg); }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ image: url({assets}/check_on.svg); }}
QCheckBox::indicator:unchecked:disabled, QAbstractItemView::indicator:unchecked:disabled {{ image: url({assets}/check_off_dis.svg); }}
QCheckBox::indicator:checked:disabled, QAbstractItemView::indicator:checked:disabled {{ image: url({assets}/check_on_dis.svg); }}
QRadioButton::indicator {{ width: 16px; height: 16px; }}
QRadioButton::indicator:unchecked {{ image: url({assets}/radio_off.svg); }}
QRadioButton::indicator:checked {{ image: url({assets}/radio_on.svg); }}
QRadioButton::indicator:disabled {{ image: url({assets}/radio_dis.svg); }}

QAbstractItemView {{
    background: {base}; alternate-background-color: {alt}; border: 1px solid {border}; border-radius: 8px;
    selection-background-color: {sel}; selection-color: {text}; gridline-color: {border_soft};
}}
QTreeWidget#sidebar, QListView#grid {{ border-radius: 0; }}
QAbstractItemView::item {{ padding: 2px 4px; }}
QAbstractItemView::item:hover {{ background: {hover}; }}
QAbstractItemView::item:selected {{ background: {sel}; color: {text}; }}
QListView#grid {{ border: none; background: {window}; }}
QListView#grid::item {{ border-radius: 8px; padding: 6px; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:has-children:closed {{ image: url({assets}/right.svg); }}
QTreeView::branch:has-children:open {{ image: url({assets}/down.svg); }}
QHeaderView {{ background: {base}; border: none; }}
QHeaderView::section {{
    background: {base}; color: {muted}; border: none; border-bottom: 1px solid {border};
    border-right: 1px solid {border_soft}; padding: 6px 8px; font-weight: bold;
}}
QHeaderView::section:last {{ border-right: none; }}
QHeaderView::down-arrow {{ image: url({assets}/down.svg); width: 10px; height: 10px; }}
QHeaderView::up-arrow {{ image: url({assets}/up.svg); width: 10px; height: 10px; }}
QTableCornerButton::section {{ background: {base}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {scroll}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QGroupBox {{
    background: {surface}; border: 1px solid {border}; border-radius: 10px;
    margin-top: 22px; padding: 14px 12px 12px 12px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 4px; top: 0; padding: 0; color: {muted}; font-weight: bold; }}
QGroupBox:disabled {{ color: {faint}; }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {base}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 7px 14px; border: none; border-bottom: 2px solid transparent; margin-right: 4px; }}
QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}

QProgressBar {{ background: {track}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QStatusBar {{ background: {window}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar QLabel {{ color: {muted}; padding: 2px 4px; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}

QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 6px 8px; }}
QMenu {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {sel}; color: {text}; }}
QMenu::item:disabled {{ color: {faint}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QMenu::right-arrow {{ image: url({assets}/right.svg); width: 12px; height: 12px; }}

QSlider::groove:horizontal {{ height: 4px; background: {track}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {accent_hover}; }}
QToolButton#zoomSmall, QToolButton#zoomLarge {{ border: none; background: transparent; border-radius: 4px; padding: 2px; }}
QToolButton#zoomSmall:hover, QToolButton#zoomLarge:hover {{ background: {hover}; }}
QToolButton#zoomSmall {{ qproperty-icon: url({assets}/thumb_small.svg); }}
QToolButton#thumbPrev, QToolButton#thumbNext {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; }}
QToolButton#thumbPrev:hover, QToolButton#thumbNext:hover {{ background: {hover}; border-color: {border_strong}; }}
QToolButton#thumbPrev {{ qproperty-icon: url({assets}/left.svg); }}
QToolButton#thumbNext {{ qproperty-icon: url({assets}/right.svg); }}
QToolButton#zoomLarge {{ qproperty-icon: url({assets}/thumb_large.svg); }}
)QSS";

// str.format 과 같은 치환.
static QString formatQss(const Theme& t, const QString& assets) {
    const QString src = QString::fromUtf8(QSS);
    QString out;
    out.reserve(src.size());
    for (int i = 0; i < src.size(); ++i) {
        const QChar ch = src[i];
        if (ch == u'{' && i + 1 < src.size() && src[i + 1] == u'{') {
            out += u'{';
            ++i;
        } else if (ch == u'}' && i + 1 < src.size() && src[i + 1] == u'}') {
            out += u'}';
            ++i;
        } else if (ch == u'{') {
            const int end = src.indexOf(u'}', i);
            const QString name = src.mid(i + 1, end - i - 1);
            out += name == u"assets"_s ? assets : t.value(name);
            i = end;
        } else {
            out += ch;
        }
    }
    return out;
}

QString resolveTheme(const QString& theme) {
    if (themes().contains(theme))
        return theme;
    return QGuiApplication::styleHints()->colorScheme() == Qt::ColorScheme::Light ? u"light"_s : u"dark"_s;
}

void applyTheme(const QString& theme) {
    g_theme = resolveTheme(theme);
    const Theme& t = themes()[g_theme];
    QApplication::setStyle(u"Fusion"_s);
    QFont font;
    font.setFamilies(uiFamilies());
    font.setPointSizeF(9.5);
    QApplication::setFont(font);
    QPalette pal;
    const QList<std::pair<QPalette::ColorRole, const char*>> roles = {
        {QPalette::Window, "window"}, {QPalette::WindowText, "text"}, {QPalette::Base, "base"},
        {QPalette::AlternateBase, "alt"}, {QPalette::ToolTipBase, "surface"}, {QPalette::ToolTipText, "text"},
        {QPalette::Text, "text"}, {QPalette::Button, "button"}, {QPalette::ButtonText, "text"},
        {QPalette::Highlight, "accent"}, {QPalette::HighlightedText, "on_accent"},
        {QPalette::PlaceholderText, "faint"}, {QPalette::Link, "accent"}, {QPalette::Mid, "border"},
        {QPalette::Dark, "border_strong"}};
    for (const auto& [role, key] : roles)
        pal.setColor(role, QColor(t.value(QString::fromLatin1(key))));
    for (auto role : {QPalette::Text, QPalette::ButtonText, QPalette::WindowText})
        pal.setColor(QPalette::Disabled, role, QColor(t.value(u"faint"_s)));
    QApplication::setPalette(pal);
    qApp->setStyleSheet(formatQss(t, writeAssets(t)));
}

QLabel* mutedLabel(const QString& text) {
    auto* lbl = new QLabel(text);
    lbl->setObjectName(u"muted"_s);
    lbl->setWordWrap(true);
    return lbl;
}

QWidget* pageTitle(const QString& text, const QString& sub, bool alwaysSub) {
    auto* w = new QWidget;
    auto* l = new QVBoxLayout(w);
    l->setContentsMargins(0, 0, 0, 6);
    auto* t = new QLabel(text);
    t->setObjectName(u"h1"_s);
    l->addWidget(t);
    if (alwaysSub || !sub.isEmpty()) {
        auto* s = new QLabel(sub);
        s->setObjectName(u"muted"_s);
        s->setWordWrap(true);
        l->addWidget(s);
    }
    return w;
}

// ---------------------------------------------------------------- loading.py

static int g_busy = 0;

bool isBusy() {
    return g_busy > 0;
}

namespace {

class LoadingOverlay : public QWidget {
public:
    LoadingOverlay(QWidget* window, const QString& titleText, Quips quipSet, bool showCount)
        : QWidget(window), window_(window), showCount_(showCount), quips_(quipsFor(quipSet)),
          showSteps_(quipSet == Quips::Organize || quipSet == Quips::Import || quipSet == Quips::Bisect) {
        setAttribute(Qt::WA_StyledBackground, false);
        setFocusPolicy(Qt::StrongFocus);
        card = new QFrame(this);
        card->setObjectName(u"card"_s);
        card->setFixedWidth(440);
        auto* lay = new QVBoxLayout(card);
        lay->setContentsMargins(24, 20, 24, 20);
        lay->setSpacing(10);
        title = new QLabel(titleText);
        title->setObjectName(u"h2"_s);
        lay->addWidget(title);
        auto* row = new QHBoxLayout;
        phase = new QLabel(u"준비 중…"_s);
        phase->setObjectName(u"muted"_s);
        count = new QLabel(QString());
        count->setObjectName(u"muted"_s);
        row->addWidget(phase, 1);
        row->addWidget(count);
        lay->addLayout(row);
        bar = new QProgressBar;
        bar->setObjectName(u"gauge"_s);
        bar->setTextVisible(false);
        bar->setRange(0, 0);
        lay->addWidget(bar);
        auto* bottom = new QHBoxLayout;
        detail = new QLabel(QString());
        detail->setObjectName(u"muted"_s);
        percent = new QLabel(QString());
        percent->setObjectName(u"percent"_s);
        percent->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
        bottom->addWidget(detail, 1);
        bottom->addWidget(percent);
        count->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
        count->setMinimumWidth(count->fontMetrics().horizontalAdvance(u"000000 / 000000"_s));
        percent->setMinimumWidth(percent->fontMetrics().horizontalAdvance(u"100%"_s) + 12);
        lay->addLayout(bottom);
        window->installEventFilter(this);
        if (!quips_.isEmpty()) {
            nextQuip();
            auto* timer = new QTimer(this);
            connect(timer, &QTimer::timeout, this, [this] { nextQuip(); });
            timer->start(2500);
        }
        fit();
    }

    // 같은 멘트가 연달아 나오지 않게 무작위로 고른다.
    void nextQuip() {
        int i = int(QRandomGenerator::global()->bounded(quips_.size()));
        if (i == lastQuip_ && quips_.size() > 1)
            i = (i + 1 + int(QRandomGenerator::global()->bounded(quips_.size() - 1))) % int(quips_.size());
        lastQuip_ = i;
        phase->setText(quips_[i]);
    }

    void fit() {
        setGeometry(parentWidget()->rect());
        card->adjustSize();
        card->move((width() - card->width()) / 2, (height() - card->height()) / 2);
    }

    bool eventFilter(QObject*, QEvent* e) override {
        if (e->type() == QEvent::Resize)
            fit();
        return false;
    }

    void setProgress(int i, int n, const QString& text, const QString& phaseText) {
        if (quips_.isEmpty()) {
            if (!phaseText.isEmpty())
                phase->setText(phaseText);
        } else if (showSteps_ && !phaseText.isEmpty()) {
            step_ = phaseText;   // 멘트를 쓰는 창은 단계 이름을 아래 줄 앞에 붙인다
        }
        if (n > 0) {
            bar->setRange(0, n);
            bar->setValue(qMin(i, n));
            if (showCount_)
                count->setText(u"%1 / %2"_s.arg(qMin(i, n)).arg(n));
            percent->setText(u"%1%"_s.arg(int(100.0 * qMin(i, n) / n)));
        }
        QString name;
        if (!text.isEmpty()) {
            QString trimmed = text;
            while (trimmed.endsWith(u'/') || trimmed.endsWith(u'\\'))
                trimmed.chop(1);
            const int cut = qMax(trimmed.lastIndexOf(u'/'), trimmed.lastIndexOf(u'\\'));
            name = trimmed.mid(cut + 1);
        }
        const QFontMetrics fm = detail->fontMetrics();
        if (step_.isEmpty()) {
            detail->setText(fm.elidedText(name, Qt::ElideMiddle, 300));
        } else {
            const QString head = name.isEmpty() ? step_ : step_ + u" · "_s;
            detail->setText(head + fm.elidedText(name, Qt::ElideMiddle, qMax(60, 300 - fm.horizontalAdvance(head))));
        }
    }

    QWidget* window_;
    bool showCount_ = true;

protected:
    void paintEvent(QPaintEvent*) override {
        QColor dim(tok("window"));
        dim.setAlpha(200);
        QPainter(this).fillRect(rect(), dim);
    }
    void mousePressEvent(QMouseEvent* e) override { e->accept(); }
    void keyPressEvent(QKeyEvent* e) override { e->accept(); }

private:
    QFrame* card;
    QLabel *title, *phase, *count, *detail, *percent;
    QStringList quips_;
    bool showSteps_;
    int lastQuip_ = -1;
    QString step_;
    QProgressBar* bar;
};

}

QJsonValue runWithLoading(QWidget* parent, const QString& title, const std::function<QJsonValue(const LoadProgress&)>& fn, Quips quips, bool showCount) {
    QWidget* window = parent->window();
    auto* overlay = new LoadingOverlay(window, title, quips, showCount);
    overlay->show();
    overlay->raise();
    overlay->setFocus();
    QPointer<LoadingOverlay> guard(overlay);
    QEventLoop loop;
    QJsonValue result;
    QString error;
    bool failed = false;
    const LoadProgress progress = [guard](int i, int n, const QString& text, const QString& phase) {
        if (guard)
            postTo(guard.data(), [guard, i, n, text, phase] {
                if (guard)
                    guard->setProgress(i, n, text, phase);
            });
    };
    ++g_busy;
    std::thread worker([&] {
        try {
            result = fn(progress);
        } catch (const CoreError& ex) {
            failed = true;
            error = ex.msg;
        } catch (const std::exception& ex) {
            failed = true;
            error = QString::fromUtf8(ex.what());
        }
        postTo(&loop, [&loop] { loop.quit(); });
    });
    loop.exec();
    worker.join();
    --g_busy;
    window->removeEventFilter(overlay);
    overlay->deleteLater();   // Python 판과 같이 숨기지 않고 지연 삭제만 한다
    if (failed)
        throw CoreError(error);
    return result;
}
