#include "pages.h"

#include "dialogs.h"

static const QString SAME_FILE = u"same_file"_s;
static const QString SAME_ITEM = u"same_item"_s;

static std::pair<QWidget*, QCheckBox*> centeredCheck(bool checked) {
    auto* cell = new QWidget;
    auto* chk = new QCheckBox;
    chk->setChecked(checked);
    auto* box = new QHBoxLayout(cell);
    box->setContentsMargins(0, 0, 0, 0);
    box->addWidget(chk, 0, Qt::AlignCenter);
    return {cell, chk};
}

static bool gameRunning() {
    return coreCall("launcher.running").toBool();
}

static double now() {
    return QDateTime::currentMSecsSinceEpoch() / 1000.0;
}

// ---------------------------------------------------------------- OrganizePage

OrganizePage::OrganizePage() {
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(u"자동 정리"_s, u"CC템을 분석하여 자동으로 분류합니다"_s));
    table = new QTableWidget(0, 5);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->setHorizontalHeaderLabels({u"이동"_s, u"이름"_s, u"분류"_s, u"현재 위치 → 이동 위치"_s, u"판정 근거"_s});
    QHeaderView* header = table->horizontalHeader();
    header->setSectionResizeMode(QHeaderView::Interactive);
    header->setStretchLastSection(true);
    int col = 0;
    for (int width : {44, 300, 200, 420})
        table->setColumnWidth(col++, width);
    header->setSectionResizeMode(0, QHeaderView::Fixed);
    table->verticalHeader()->hide();
    table->verticalHeader()->setDefaultSectionSize(38);
    table->setShowGrid(false);
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    lay->addWidget(table, 1);
    auto* row = new QHBoxLayout;
    summary = new QLabel;
    row->addWidget(summary, 1);
    auto addButton = [&](const QString& text, std::function<void()> fn) {
        auto* b = new QPushButton(text);
        connect(b, &QPushButton::clicked, this, fn);
        row->addWidget(b);
    };
    addButton(u"모두 선택"_s, [this] { checkAll(true); });
    addButton(u"모두 해제"_s, [this] { checkAll(false); });
    addButton(u"목록 새로고침"_s, [this] { refreshPage(); });
    bApply = new QPushButton(u"자동 정리 시작"_s);
    bApply->setObjectName(u"primary"_s);
    connect(bApply, &QPushButton::clicked, this, [this] { apply(); });
    row->addWidget(bApply);
    lay->addLayout(row);
}

void OrganizePage::refreshPage() {
    plans.clear();
    for (const auto& v : coreCall("lib.organize_plan").toArray())
        plans.append({itemFrom(v.toObject()["item"_L1]), v.toObject()["target_rel"_L1].toString()});
    table->setRowCount(int(plans.size()));
    checks.clear();
    for (int r = 0; r < plans.size(); ++r) {
        const Plan& p = plans[r];
        auto [cell, chk] = centeredCheck(true);
        checks << chk;
        table->setCellWidget(r, 0, cell);
        table->setItem(r, 1, new QTableWidgetItem(p.item->name));
        table->removeCellWidget(r, 2);
        if (p.item->kind == u"package"_s) {
            auto* combo = new CategoryCombo;
            combo->setKey(p.item->category);
            connect(combo, &QComboBox::activated, this, [this, r, combo] { recat(r, combo); });
            table->setCellWidget(r, 2, combo);
            delete table->takeItem(r, 2);
        } else {
            table->setItem(r, 2, new QTableWidgetItem(cats::label(cats::SCRIPT_MOD)));
        }
        const QString cur = rsplitHead(p.item->relPath);
        table->setItem(r, 3, new QTableWidgetItem(u"%1  →  %2"_s.arg(cur, rsplitHead(p.targetRel))));
        auto* reason = new QTableWidgetItem(p.item->reason);
        if (p.item->confidence < 0.6 && !p.item->userSet) {
            reason->setForeground(QColor(tok("warn")));
            reason->setText(u"확인 필요 · "_s + p.item->reason);
        }
        table->setItem(r, 4, reason);
    }
    summary->setText(plans.isEmpty() ? u"모든 CC가 이미 정리되어 있습니다."_s : u"이동할 CC %1개"_s.arg(plans.size()));
    bApply->setEnabled(!plans.isEmpty());
}

void OrganizePage::recat(int row, CategoryCombo* combo) {
    Plan& p = plans[row];
    fillItem(*p.item, coreCall("lib.set_category", {{"id"_L1, p.item->id}, {"key"_L1, combo->key()}, {"move"_L1, false}}).toObject());
    p.targetRel = u"Mods/"_s + coreCall("lib.folder_for", {{"key"_L1, combo->key()}}).toString() + u'/' + rsplitTail(p.item->relPath);
    const QString cur = rsplitHead(p.item->relPath);
    table->item(row, 3)->setText(u"%1  →  %2"_s.arg(cur, rsplitHead(p.targetRel)));
    table->item(row, 4)->setText(u"직접 지정"_s);
    table->item(row, 4)->setForeground(palette().text().color());
}

void OrganizePage::checkAll(bool on) {
    for (QCheckBox* chk : checks)
        chk->setChecked(on);
}

void OrganizePage::apply() {
    QJsonArray chosen;
    for (int i = 0; i < plans.size() && i < checks.size(); ++i)
        if (checks[i]->isChecked())
            chosen << QJsonObject{{"id"_L1, plans[i].item->id}, {"target_rel"_L1, plans[i].targetRel}};
    if (chosen.isEmpty())
        return;
    if (!confirm(this, u"자동 정리"_s, u"CC %1개를 분류 폴더로 옮길까요?\n(설정의 '자동 정리 되돌리기'로 원래대로 돌릴 수 있습니다)"_s.arg(chosen.size())))
        return;
    const QString label = u"%1 (%2개)"_s.arg(core().organizeLabel).arg(chosen.size());
    int n = 0;
    try {
        n = runWithLoading(this, u"자동 정리 중"_s, [&](const LoadProgress& progress) {
            const QJsonValue moved = coreCall("lib.apply_moves", {{"plans"_L1, chosen}, {"label"_L1, label}},
                                              phased(progress, u"파일을 분류 폴더로 옮기는 중"_s));
            coreCall("lib.scan", {}, phased(progress, u"옮긴 파일을 목록에 반영하는 중"_s));
            return moved;
        }, Quips::Organize).toInt();
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"자동 정리"_s, u"정리 중 오류: "_s + ex.msg);
        n = 0;
    }
    if (changed)
        changed();
    refreshPage();
    QMessageBox::information(this, u"자동 정리"_s, u"%1개 파일을 옮겼습니다."_s.arg(n));
}

// ---------------------------------------------------------------- ResourceCfgPage

ResourceCfgPage::ResourceCfgPage() {
    cfg_ = coreCall("rc.default").toObject();
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(
        u"Resource.cfg 설정"_s,
        u"Resource.cfg는 게임이 Mods 폴더 안에서 어떤 CC 파일을 읽을지 정해 두는 파일입니다.\n"
        u"\n"
        u"우선순위: 숫자가 클수록 먼저 적용됩니다. 같은 아이템을 바꾸는 CC가 두 개 있으면 숫자가 큰 쪽이 적용됩니다\n"
        u"폴더: 규칙을 적용할 폴더입니다. 비워두면 Mods 폴더 전체입니다.\n"
        u"하위 폴더 깊이: 폴더 안으로 몇 단계까지 들어가서 읽을지입니다. (0 = 그 폴더만, 최대 5)\n"
        u"\n"
        u"잘 모르겠으면 [기본값으로]를 누르고 [저장]하거나 그냥 건드리지마세요"_s));

    auto* body = new QHBoxLayout;
    auto* left = new QVBoxLayout;
    table = new QTableWidget(0, 4);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->setHorizontalHeaderLabels({u"우선순위"_s, u"폴더 (비워두면 Mods 전체)"_s, u"하위 폴더 깊이"_s, QString()});
    table->horizontalHeader()->setSectionResizeMode(QHeaderView::Interactive);
    table->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Fixed);
    int col = 0;
    for (int width : {90, 340, 120, 70})
        table->setColumnWidth(col++, width);
    table->verticalHeader()->hide();
    table->verticalHeader()->setDefaultSectionSize(40);
    table->setShowGrid(false);
    left->addWidget(table, 1);
    auto* r = new QHBoxLayout;
    auto* bAdd = new QPushButton(u"규칙 추가"_s);
    connect(bAdd, &QPushButton::clicked, this, [this] { addRow(500, QString(), 4); });
    r->addWidget(bAdd);
    r->addStretch(1);
    left->addLayout(r);

    auto* opt = new QGroupBox(u"게임 옵션 (Options.ini)"_s);
    auto* ol = new QVBoxLayout(opt);
    chkCc = new QCheckBox(u"커스텀 콘텐츠와 모드 허용"_s);
    chkScript = new QCheckBox(u"스크립트 모드 허용"_s);
    optStatus = new QLabel;
    optStatus->setObjectName(u"muted"_s);
    ol->addWidget(chkCc);
    ol->addWidget(chkScript);
    ol->addWidget(optStatus);
    auto* bOpt = new QPushButton(u"게임 옵션 저장"_s);
    connect(bOpt, &QPushButton::clicked, this, [this] { saveOptions(); });
    ol->addWidget(bOpt, 0, Qt::AlignLeft);
    left->addWidget(opt);
    body->addLayout(left, 3);

    auto* right = new QVBoxLayout;
    right->addWidget(new QLabel(u"미리보기 (저장될 내용)"_s));
    preview = new QPlainTextEdit;
    preview->setReadOnly(true);
    preview->setFont(QFont(u"Consolas"_s, 9));
    right->addWidget(preview, 1);
    body->addLayout(right, 2);
    lay->addLayout(body, 1);

    auto* bot = new QHBoxLayout;
    status = new QLabel;
    bot->addWidget(status, 1);
    auto* bReload = new QPushButton(u"다시 읽기"_s);
    connect(bReload, &QPushButton::clicked, this, [this] { refreshPage(); });
    bot->addWidget(bReload);
    auto* bDefaults = new QPushButton(u"기본값으로"_s);
    connect(bDefaults, &QPushButton::clicked, this, [this] { defaults(); });
    bot->addWidget(bDefaults);
    auto* bSave = new QPushButton(u"저장 (자동 백업)"_s);
    bSave->setObjectName(u"primary"_s);
    connect(bSave, &QPushButton::clicked, this, [this] { save(); });
    bot->addWidget(bSave);
    lay->addLayout(bot);
}

QStringList ResourceCfgPage::folders() const {
    QStringList out;
    const QString mods = cfg().modsDir;
    if (isDir(mods)) {
        out = QDir(mods).entryList(QDir::Dirs | QDir::NoDotAndDotDot | QDir::Hidden | QDir::System, QDir::NoSort);
        std::sort(out.begin(), out.end());
    }
    return out;
}

void ResourceCfgPage::addRow(int priority, const QString& folder, int depth) {
    const int r = table->rowCount();
    table->insertRow(r);
    auto* pr = new QSpinBox;
    pr->setRange(-10000, 10000);
    pr->setValue(priority);
    auto* folderBox = new QComboBox;
    folderBox->setEditable(true);
    folderBox->addItems(QStringList{QString()} + folders());
    folderBox->setCurrentText(folder);
    auto* depthBox = new QSpinBox;
    depthBox->setRange(0, core().maxDepth);
    depthBox->setValue(depth);
    for (QSpinBox* w : {pr, depthBox})
        connect(w, &QSpinBox::valueChanged, this, [this] { updatePreview(); });
    connect(folderBox, &QComboBox::currentTextChanged, this, [this] { updatePreview(); });
    table->setCellWidget(r, 0, pr);
    table->setCellWidget(r, 1, folderBox);
    table->setCellWidget(r, 2, depthBox);
    auto* bDel = new QPushButton(u"삭제"_s);
    connect(bDel, &QPushButton::clicked, this, [this, bDel] { delRow(bDel); });
    table->setCellWidget(r, 3, bDel);
    updatePreview();
}

void ResourceCfgPage::delRow(QPushButton* button) {
    for (int r = 0; r < table->rowCount(); ++r) {
        if (table->cellWidget(r, 3) == button) {
            table->removeRow(r);
            break;
        }
    }
    updatePreview();
}

static QString stripSlashes(QString s) {
    s = s.trimmed();
    while (s.startsWith(u'\\') || s.startsWith(u'/'))
        s.remove(0, 1);
    while (s.endsWith(u'\\') || s.endsWith(u'/'))
        s.chop(1);
    return s;
}

QJsonObject ResourceCfgPage::collect() const {
    QJsonArray rules;
    for (int r = 0; r < table->rowCount(); ++r) {
        auto* pr = qobject_cast<QSpinBox*>(table->cellWidget(r, 0));
        auto* folderBox = qobject_cast<QComboBox*>(table->cellWidget(r, 1));
        auto* depthBox = qobject_cast<QSpinBox*>(table->cellWidget(r, 2));
        if (!pr || !folderBox || !depthBox)
            continue;
        rules << QJsonArray{pr->value(), stripSlashes(folderBox->currentText()), depthBox->value()};
    }
    return {{"rules"_L1, rules}, {"extra"_L1, cfg_["extra"_L1]}};
}

void ResourceCfgPage::updatePreview() {
    preview->setPlainText(coreCall("rc.render", collect()).toString());
}

void ResourceCfgPage::loadCfg(const QJsonObject& cfg) {
    cfg_ = cfg;
    table->setRowCount(0);
    for (const auto& rv : cfg["rules"_L1].toArray()) {
        const QJsonArray rule = rv.toArray();
        addRow(rule[0].toInt(), rule[1].toString(), rule[2].toInt());
    }
    updatePreview();
}

void ResourceCfgPage::refreshPage() {
    const QString mods = cfg().modsDir;
    const QJsonObject loaded = coreCall("rc.load", {{"mods"_L1, mods}}).toObject();
    loadCfg(loaded);
    status->setText(loaded["has_text"_L1].toBool() ? u"Resource.cfg를 읽었습니다."_s
                                                   : u"Resource.cfg가 없어 기본값을 표시합니다. 저장하면 새로 만듭니다."_s);
    checkOverridesRule();
    const QJsonObject opts = coreCall("rc.read_options", {{"sims"_L1, cfg().simsDir}}).toObject();
    chkCc->setChecked(opts.value("modsdisabled"_L1).toString(u"0"_s) == u"0"_s);
    chkScript->setChecked(opts.value("scriptmodsenabled"_L1).toString(u"0"_s) == u"1"_s);
    chkCc->setEnabled(!opts.isEmpty());
    chkScript->setEnabled(!opts.isEmpty());
    optStatus->setText(opts.isEmpty() ? u"Options.ini를 찾지 못했습니다 (게임을 한 번 실행하면 생깁니다)."_s
                                      : u"게임이 꺼져 있을 때만 변경하세요. 게임 업데이트 후에는 이 값이 꺼질 수 있습니다."_s);
}

void ResourceCfgPage::checkOverridesRule() {
    const QString mods = cfg().modsDir;
    bool hasRule = false;
    for (const auto& rv : cfg_["rules"_L1].toArray())
        hasRule = hasRule || rv.toArray()[1].toString().toLower() == u"00_overrides"_s;
    if (isDir(mods + u"\\00_Overrides"_s) && !hasRule)
        status->setText(status->text() + u"  00_Overrides 폴더에 높은 우선순위 규칙이 없습니다. '기본값으로'를 눌러보세요."_s);
}

void ResourceCfgPage::defaults() {
    QJsonObject cfg = coreCall("rc.default").toObject();
    cfg["extra"_L1] = cfg_["extra"_L1];
    loadCfg(cfg);
    status->setText(u"기본값을 불러왔습니다. 저장해야 적용됩니다."_s);
}

void ResourceCfgPage::save() {
    const QJsonObject collected = collect();
    if (collected["rules"_L1].toArray().isEmpty()) {
        QMessageBox::warning(this, u"Resource.cfg"_s, u"규칙이 하나도 없으면 게임이 CC를 읽지 않습니다."_s);
        return;
    }
    QJsonObject args = collected;
    args["mods"_L1] = cfg().modsDir;
    const QString backup = coreCall("rc.save", args).toString();
    cfg_ = collected;
    status->setText(u"저장했습니다."_s + (backup.isEmpty() ? QString() : u" 백업: "_s + backup));
}

void ResourceCfgPage::saveOptions() {
    if (gameRunning()) {
        QMessageBox::warning(this, u"게임 옵션"_s, u"게임이 실행 중입니다. 게임을 끈 뒤 저장하세요."_s);
        return;
    }
    const QString sims = cfg().simsDir;
    const bool ok1 = coreCall("rc.set_option", {{"sims"_L1, sims}, {"key"_L1, u"modsdisabled"_s}, {"value"_L1, chkCc->isChecked() ? u"0"_s : u"1"_s}}).toBool();
    const bool ok2 = coreCall("rc.set_option", {{"sims"_L1, sims}, {"key"_L1, u"scriptmodsenabled"_s}, {"value"_L1, chkScript->isChecked() ? u"1"_s : u"0"_s}}).toBool();
    optStatus->setText(ok1 && ok2 ? u"게임 옵션을 저장했습니다 (Options.ini.s4ccm.bak 백업)."_s : u"저장 실패"_s);
}

// ---------------------------------------------------------------- BisectPage

BisectPage::BisectPage() {
    watchTimer = new QTimer(this);
    watchTimer->setInterval(3000);
    connect(watchTimer, &QTimer::timeout, this, [this] { watchGame(); });

    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(
        u"충돌 테스트 (절반 빼기)"_s,
        u"게임을 망가뜨리는 CC/모드를 찾는 방법입니다.\n"
        u"\n"
        u"1. [절반 빼고 테스트 시작]을 누르면 모드의 절반을 무작위로 Mods 밖으로 뺍니다.\n"
        u"2. 심즈를 켜서 확인한 뒤 [정상 작동] 또는 [문제 발생]을 누릅니다.\n"
        u"   • 정상 작동 → 문제는 뺀 쪽에 있습니다. 뺐던 모드의 절반을 다시 넣습니다.\n"
        u"   • 문제 발생 → 문제는 남은 쪽에 있습니다. 적용된 모드에서 절반을 다시 뺍니다.\n"
        u"3. 2번을 반복하면 원인 모드 하나가 남습니다."_s));

    setupBox = new QGroupBox(u"테스트 범위"_s);
    auto* sl = new QGridLayout(setupBox);
    scope = new QButtonGroup(this);
    rbAll = new QRadioButton(u"전체 (지금 모드 폴더에 있는 모든 CC와 모드)"_s);
    rbCat = new QRadioButton(u"특정 분류만:"_s);
    rbRecent = new QRadioButton(u"최근 추가한 것만:"_s);
    int i = 0;
    for (QRadioButton* rb : {rbAll, rbCat, rbRecent})
        scope->addButton(rb, i++);
    rbAll->setChecked(true);
    catCombo = new QComboBox;
    for (const auto& [g, glabel] : cats::GROUPS) {
        catCombo->addItem(u"[%1]"_s.arg(glabel), u"group:"_s + g);
        for (const auto& c : cats::CATEGORIES)
            if (c.group == g && g != cats::UNSORTED && g != u"tuning"_s && g != u"animations"_s && g != u"overrides"_s)
                catCombo->addItem(u"    "_s + c.label, u"cat:"_s + c.key);
    }
    days = new QSpinBox;
    days->setRange(1, 365);
    days->setValue(7);
    days->setSuffix(u" 일 이내"_s);
    chkScripts = new QCheckBox(u"스크립트 모드 포함"_s);
    chkScripts->setChecked(true);
    sl->addWidget(rbAll, 0, 0, 1, 2);
    sl->addWidget(rbCat, 1, 0);
    sl->addWidget(catCombo, 1, 1);
    sl->addWidget(rbRecent, 2, 0);
    sl->addWidget(days, 2, 1);
    sl->addWidget(chkScripts, 3, 0, 1, 2);
    bStart = new QPushButton(u"절반 빼고 테스트 시작"_s);
    bStart->setObjectName(u"primary"_s);
    connect(bStart, &QPushButton::clicked, this, [this] { start(); });
    sl->addWidget(bStart, 4, 0, 1, 2, Qt::AlignLeft);
    lay->addWidget(setupBox);

    runBox = new QGroupBox(u"진행"_s);
    auto* rl = new QVBoxLayout(runBox);
    progressLabel = new QLabel;
    progressLabel->setObjectName(u"h1"_s);
    progress = new QProgressBar;
    progress->setTextVisible(false);
    rl->addWidget(progressLabel);
    rl->addWidget(progress);
    hint = new QLabel;
    hint->setWordWrap(true);
    rl->addWidget(hint);
    auto* br = new QHBoxLayout;
    bLaunch = new QPushButton(u"게임 실행"_s);
    bLaunch->setObjectName(u"primary"_s);
    connect(bLaunch, &QPushButton::clicked, this, [this] { launch(); });
    bOk = new QPushButton(u"정상 작동"_s);
    connect(bOk, &QPushButton::clicked, this, [this] { report(true); });
    bBad = new QPushButton(u"문제 발생"_s);
    connect(bBad, &QPushButton::clicked, this, [this] { report(false); });
    bEnd = new QPushButton(u"테스트 종료 · 전부 원위치"_s);
    bEnd->setObjectName(u"danger"_s);
    connect(bEnd, &QPushButton::clicked, this, [this] { end(); });
    for (QPushButton* b : {bLaunch, bOk, bBad})
        br->addWidget(b);
    br->addStretch(1);
    br->addWidget(bEnd);
    rl->addLayout(br);
    tabs = new QTabWidget;
    listIn = new QListWidget;
    listOut = new QListWidget;
    listCleared = new QListWidget;
    tabs->addTab(listIn, u"의심 대상 · 적용 중"_s);
    tabs->addTab(listOut, u"의심 대상 · 빠져 있음"_s);
    tabs->addTab(listCleared, u"문제 없음 확인"_s);
    rl->addWidget(tabs, 1);
    lay->addWidget(runBox, 1);
}

QJsonObject BisectPage::state() const {
    return coreCall("bisect.state").toObject();
}

QList<ItemPtr> BisectPage::scopeItems() {
    QList<ItemPtr> items;
    for (const auto& it : itemsFrom(coreCall("lib.items")))
        if (it->status == u"active"_s)
            items << it;
    auto keep = [&items](const std::function<bool(const Item&)>& pred) {
        QList<ItemPtr> out;
        for (const auto& it : items)
            if (pred(*it))
                out << it;
        items = out;
    };
    if (!chkScripts->isChecked())
        keep([](const Item& i) { return i.kind != u"script"_s; });
    const int sid = scope->checkedId();
    if (sid == 1) {
        const QString data = catCombo->currentData().toString();
        const int cut = int(data.indexOf(u':'));
        const QString kind = data.left(cut), key = data.mid(cut + 1);
        keep([&](const Item& i) { return kind == u"group"_s ? cats::groupOf(i.category) == key : i.category == key; });
    } else if (sid == 2) {
        const double cutoff = now() - days->value() * 86400.0;
        keep([cutoff](const Item& i) { return i.addedAt >= cutoff || i.mtime >= cutoff; });
    }
    return items;
}

bool BisectPage::gameClosedOrWarn(const QString& what) {
    if (gameRunning()) {
        QMessageBox::warning(this, u"충돌 테스트"_s, u"게임이 실행 중입니다. 게임을 끈 뒤 %1."_s.arg(what));
        return false;
    }
    return true;
}

void BisectPage::start() {
    const QList<ItemPtr> items = scopeItems();
    if (items.size() < 2) {
        QMessageBox::information(this, u"충돌 테스트"_s, u"테스트할 대상이 2개 이상이어야 합니다."_s);
        return;
    }
    if (!gameClosedOrWarn(u"시작하세요"_s))
        return;
    if (!confirm(this, u"충돌 테스트"_s,
                 u"%1개 중 절반(%2개)을 무작위로 Mods 밖으로 뺍니다.\n테스트를 시작할까요?"_s.arg(items.size()).arg(items.size() / 2)))
        return;
    const int n = run(u"충돌 테스트 준비 중"_s, u"모드 절반을 Mods 밖으로 빼는 중"_s, "bisect.start", {{"ids"_L1, idsOf(items)}});
    if (changed)
        changed();
    refreshPage();
    askResult(u"모드 %1개 중 절반(%2개)을 뺐습니다."_s.arg(items.size()).arg(n));
}

void BisectPage::launch() {
    const QString exe = cfg().gameExe;
    if (exe.isEmpty() || !isFile(exe)) {
        QMessageBox::warning(this, u"게임 실행"_s, u"게임 실행 파일을 찾지 못했습니다. 설정에서 지정하세요.\n"
                                                   u"직접 게임을 켜서 확인한 뒤 결과 버튼을 눌러도 됩니다."_s);
        return;
    }
    coreCall("launcher.launch", {{"exe"_L1, exe}, {"sims"_L1, cfg().simsDir}, {"clear"_L1, cfg().clearThumbcacheOnLaunch}});
    watchState = u"starting"_s;
    watchStarted = now();
    watchTimer->start();
    refreshPage();
}

void BisectPage::watchGame() {
    const bool running = gameRunning();
    if (watchState == u"starting"_s) {
        if (running) {
            watchState = u"running"_s;
        } else if (now() - watchStarted > WATCH_START_TIMEOUT) {
            stopWatch();
            hint->setText(u"게임이 켜지는 것을 확인하지 못했습니다. 게임을 확인한 뒤 결과 버튼을 눌러주세요."_s);
        }
        return;
    }
    if (watchState == u"running"_s && !running) {
        stopWatch();
        askResult(u"게임이 종료되었습니다."_s);
    }
}

void BisectPage::stopWatch() {
    watchTimer->stop();
    watchState.clear();
    refreshPage();
}

void BisectPage::askResult(const QString& note) {
    const QJsonObject s = state();
    QMessageBox box(this);
    box.setWindowTitle(u"충돌 테스트 %1단계"_s.arg(s["step"_L1].toInt() + 1));
    box.setIcon(QMessageBox::Question);
    box.setText(note + u"\n심즈를 켜서 문제가 생기는지 확인해 보세요."_s);
    box.setInformativeText(u"의심 대상 %1개 · 남은 단계 약 %2\n"
                           u"확인하려던 문제(오류, 멈춤, 깨짐 등)가 없었다면 [정상 작동]을 누르세요."_s
                               .arg(s["suspects"_L1].toArray().size())
                               .arg(s["remaining_steps"_L1].toInt()));
    QAbstractButton* ok = box.addButton(u"정상 작동"_s, QMessageBox::YesRole);
    QAbstractButton* bad = box.addButton(u"문제 발생"_s, QMessageBox::NoRole);
    QAbstractButton* launchBtn = nullptr;
    if (!cfg().gameExe.isEmpty() && watchState.isEmpty()) {
        launchBtn = box.addButton(u"게임 실행"_s, QMessageBox::ActionRole);
        launchBtn->setToolTip(u"게임을 켜고, 게임을 끄면 이 창을 다시 띄웁니다."_s);
    }
    box.addButton(u"나중에"_s, QMessageBox::RejectRole);
    box.exec();
    if (box.clickedButton() == ok)
        report(true);
    else if (box.clickedButton() == bad)
        report(false);
    else if (launchBtn && box.clickedButton() == launchBtn)
        launch();
}

void BisectPage::report(bool worked) {
    if (!gameClosedOrWarn(u"결과를 눌러주세요 (파일을 옮겨야 합니다)"_s))
        return;
    const int n = run(u"다음 단계 준비 중"_s, worked ? u"뺐던 모드의 절반을 다시 넣는 중"_s : u"적용된 모드의 절반을 빼는 중"_s,
                      "bisect.report", {{"worked"_L1, worked}});
    if (changed)
        changed();
    refreshPage();
    const QJsonObject s = state();
    if (s["testing"_L1].toBool()) {
        askResult(worked ? u"뺐던 모드의 절반(%1개)을 다시 넣었습니다."_s.arg(n)
                         : u"적용된 모드에서 절반(%1개)을 다시 뺐습니다."_s.arg(n));
        return;
    }
    if (!s["found"_L1].isNull()) {
        const qint64 found = s["found"_L1].toInteger();
        const ItemPtr it = itemFrom(coreCall("lib.get", {{"id"_L1, found}}));
        QMessageBox::information(this, u"충돌 테스트 결과"_s,
                                 u"원인으로 보이는 CC/모드를 찾았습니다:\n\n%1\n%2\n\n"
                                 u"[테스트 종료 · 전부 원위치]를 누르면 빼둔 모드가 모두 돌아옵니다.\n"
                                 u"※ 두 개 이상이 함께 일으키는 충돌이면 결과가 정확하지 않을 수 있습니다."_s
                                     .arg(it ? it->name : QString::number(found), it ? it->relPath : QString()));
    } else if (!s["active"_L1].toBool()) {
        QMessageBox::information(this, u"충돌 테스트"_s, u"의심 대상이 남지 않았습니다. 범위를 넓혀 다시 시도하세요."_s);
    }
}

int BisectPage::run(const QString& title, const QString& phase, const char* method, const QJsonObject& args, bool raiseErrors) {
    try {
        return runWithLoading(this, title, [&](const LoadProgress& progress) {
            const QJsonValue result = coreCall(method, args, phased(progress, phase));
            coreCall("lib.scan", {}, phased(progress, u"목록에 반영하는 중"_s));
            return result;
        }, Quips::Bisect).toInt();
    } catch (const CoreError& ex) {
        if (raiseErrors)
            throw;
        QMessageBox::warning(this, u"충돌 테스트"_s, u"파일을 옮기는 중 오류: "_s + ex.msg);
        return 0;
    }
}

bool BisectPage::confirmLeave(bool closing) {
    const QJsonObject s = state();
    if (!s["active"_L1].toBool())
        return true;
    QMessageBox box(this);
    box.setWindowTitle(u"충돌 테스트"_s);
    box.setIcon(QMessageBox::Question);
    QString text = u"충돌 테스트가 아직 끝나지 않았습니다."_s;
    const int out = int(s["out"_L1].toArray().size());
    if (out)
        text += u"\n지금 Mods 밖에 빼 둔 모드가 %1개 있습니다."_s.arg(out);
    box.setText(text + u"\n원래대로 되돌릴까요?"_s);
    box.setInformativeText(u"원래대로 되돌리면 빼 둔 모드가 모두 Mods로 돌아가고 테스트가 끝납니다.\n"
                           u"그대로 두면 다음에 충돌 테스트 화면에서 이어서 할 수 있습니다."_s);
    QPushButton* revert = box.addButton(u"원래대로 되돌리기"_s, QMessageBox::AcceptRole);
    revert->setObjectName(u"primary"_s);
    repolish(revert);
    QAbstractButton* keep = box.addButton(closing ? u"그대로 두고 종료"_s : u"그대로 두고 나가기"_s, QMessageBox::DestructiveRole);
    box.addButton(u"취소"_s, QMessageBox::RejectRole);
    box.exec();
    if (box.clickedButton() == keep)
        return true;
    if (box.clickedButton() != revert || !gameClosedOrWarn(u"되돌리세요"_s))
        return false;
    stopWatch();
    try {
        run(u"원래대로 되돌리는 중"_s, u"빼 둔 모드를 Mods로 돌려놓는 중"_s, "bisect.end", {}, true);
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"충돌 테스트"_s, u"되돌리는 중 오류: "_s + ex.msg);
        return false;
    }
    if (changed)
        changed();
    refreshPage();
    return true;
}

void BisectPage::end() {
    if (!gameClosedOrWarn(u"종료하세요"_s))
        return;
    stopWatch();
    run(u"원래대로 되돌리는 중"_s, u"빼 둔 모드를 Mods로 돌려놓는 중"_s, "bisect.end", {});
    if (changed)
        changed();
    refreshPage();
}

void BisectPage::fill(QListWidget* lw, const QJsonArray& ids) {
    lw->clear();
    const QJsonArray names = coreCall("lib.names", {{"ids"_L1, ids}}).toArray();
    for (int i = 0; i < ids.size(); ++i) {
        const QJsonValue v = names[i];
        if (v.isArray())
            lw->addItem(u"%1   (%2)"_s.arg(v.toArray()[0].toString(), categoryPathLabel(v.toArray()[1].toString())));
        else
            lw->addItem(u"#%1 (삭제됨)"_s.arg(ids[i].toInteger()));
    }
}

void BisectPage::refreshPage() {
    const QJsonObject s = state();
    const bool active = s["active"_L1].toBool();
    const int suspects = int(s["suspects"_L1].toArray().size());
    const int cleared = int(s["cleared"_L1].toArray().size());
    setupBox->setEnabled(!active);
    runBox->setEnabled(active);
    int total = s["total"_L1].toInt();
    if (!total)
        total = suspects + cleared;
    if (!total)
        total = 1;
    if (!s["found"_L1].isNull()) {
        const qint64 found = s["found"_L1].toInteger();
        const ItemPtr it = itemFrom(coreCall("lib.get", {{"id"_L1, found}}));
        progressLabel->setText(u"원인: "_s + (it ? it->name : QString::number(found)));
        hint->setText(u"확인이 끝나면 [테스트 종료 · 전부 원위치]를 누르세요. 원인 모드는 라이브러리에서 비활성화하거나 삭제할 수 있습니다."_s);
        progress->setValue(100);
    } else if (active) {
        progressLabel->setText(u"%1단계 · 의심 대상 %2개 · 남은 단계 약 %3"_s.arg(s["step"_L1].toInt() + 1).arg(suspects).arg(s["remaining_steps"_L1].toInt()));
        progress->setValue(int(100 * (1 - double(suspects) / total)));
        if (!watchState.isEmpty())
            hint->setText(u"게임 실행 중… 확인한 뒤 게임을 끄면 정상이었는지 물어봅니다."_s);
        else
            hint->setText(u"지금 %1개가 빠져 있습니다. [게임 실행]을 눌러 확인하세요.\n"
                          u"(게임을 직접 켜서 확인했다면 아래 결과 버튼을 바로 눌러도 됩니다.)"_s.arg(s["out"_L1].toArray().size()));
    } else {
        progressLabel->setText(u"진행 중인 테스트가 없습니다"_s);
        progress->setValue(0);
        hint->setText(QString());
    }
    const bool testing = s["testing"_L1].toBool() && watchState.isEmpty();
    bOk->setEnabled(testing);
    bBad->setEnabled(testing);
    bLaunch->setEnabled(testing && !cfg().gameExe.isEmpty());
    const QJsonArray in = s["suspects_in"_L1].toArray(), out = s["suspects_out"_L1].toArray(), ok = s["cleared"_L1].toArray();
    fill(listIn, in);
    fill(listOut, out);
    fill(listCleared, ok);
    tabs->setTabText(0, u"의심 대상 · 적용 중 (%1)"_s.arg(in.size()));
    tabs->setTabText(1, u"의심 대상 · 빠져 있음 (%1)"_s.arg(out.size()));
    tabs->setTabText(2, u"문제 없음 확인 (%1)"_s.arg(ok.size()));
}

// ---------------------------------------------------------------- DuplicatesPage

DuplicatesPage::DuplicatesPage() {
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(
        u"중복 검사"_s,
        u"• 완전히 같은 파일: 같은 CC를 두 번 넣은 경우입니다. 하나만 남기면 됩니다.\n"
        u"• 같은 아이템이 들어 있는 파일: 이름은 달라도 게임 안에서 같은 아이템인 경우입니다.\n"
        u"   (예: 업데이트 전/후 버전이 둘 다 있음, 묶음 파일과 낱개 파일이 같이 있음)\n"
        u"   게임은 겹치는 아이템을 하나만 보여줍니다.\n"
        u"   단, 여러 아이템을 합친 파일은 일부만 겹칠 수 있으니 '겹치는 아이템' 칸을 보고 직접 체크하세요.\n"
        u"   ('전부'라고 나오면 그 파일의 아이템이 모두 다른 파일에도 있다는 뜻이라 지워도 됩니다.)\n"
        u"\n"
        u"완전히 같은 파일은 하나만 남기고 미리 체크해 두었습니다. 체크된 항목이 정리 대상입니다."_s));
    auto* top = new QHBoxLayout;
    bRun = new QPushButton(u"중복 검사 시작"_s);
    bRun->setObjectName(u"primary"_s);
    connect(bRun, &QPushButton::clicked, this, [this] { run(); });
    top->addWidget(bRun);
    progress = new QProgressBar;
    progress->setTextVisible(false);
    progress->hide();
    top->addWidget(progress, 1);
    summary = new QLabel;
    top->addWidget(summary, 1);
    lay->addLayout(top);

    tree = new QTreeWidget;
    tree->setHeaderLabels({u"파일"_s, u"위치"_s, u"겹치는 아이템"_s, u"크기"_s, u"수정일"_s, u"상태"_s});
    QHeaderView* header = tree->header();
    header->setSectionResizeMode(QHeaderView::Interactive);
    header->setStretchLastSection(true);
    header->setMinimumSectionSize(60);
    int col = 0;
    for (int width : {420, 260, 160, 80, 90})
        tree->setColumnWidth(col++, width);
    tree->setIconSize(QSize(40, 40));
    connect(tree, &QTreeWidget::itemDoubleClicked, this, [](QTreeWidgetItem* wi, int) {
        const ItemPtr it = wi->data(0, Qt::UserRole).value<ItemPtr>();
        if (it)
            reveal(libAbs(it->relPath));
    });
    lay->addWidget(tree, 1);

    auto* bot = new QHBoxLayout;
    bot->addStretch(1);
    bDisable = new QPushButton(u"체크한 항목 비활성화"_s);
    connect(bDisable, &QPushButton::clicked, this, [this] { apply(u"disable"_s); });
    bTrash = new QPushButton(u"체크한 항목 휴지통으로"_s);
    bTrash->setObjectName(u"danger"_s);
    connect(bTrash, &QPushButton::clicked, this, [this] { apply(u"trash"_s); });
    bot->addWidget(bDisable);
    bot->addWidget(bTrash);
    lay->addLayout(bot);
    setButtons(false);
}

void DuplicatesPage::setButtons(bool on) {
    bDisable->setEnabled(on);
    bTrash->setEnabled(on);
}

void DuplicatesPage::run() {
    if (task.active)
        return;
    tree->clear();
    summary->clear();
    progress->show();
    progress->setValue(0);
    bRun->setEnabled(false);
    failed_ = false;
    task.start(
        this,
        [this] {
            try {
                result_ = coreCall("dupes.find", {}, [this](int i, int n, const QString&) {
                    postTo(this, [this, i, n] {
                        progress->setMaximum(qMax(n, 1));
                        progress->setValue(i);
                    });
                });
            } catch (const CoreError& ex) {
                failed_ = true;
                error_ = ex.msg;
            }
        },
        [this] { done(); });
}

void DuplicatesPage::done() {
    task.join();
    progress->hide();
    bRun->setEnabled(true);
    if (failed_) {
        QMessageBox::warning(this, u"중복 검사"_s, u"검사 중 오류: "_s + error_);
        return;
    }
    show(result_.toArray());
}

void DuplicatesPage::show(const QJsonArray& groups) {
    tree->clear();
    QFont bold;
    bold.setBold(true);
    int same = 0;
    for (const auto& gv : groups) {
        const QJsonObject g = gv.toObject();
        const QString kind = g["kind"_L1].toString();
        if (kind == SAME_FILE)
            ++same;
        auto* top = new QTreeWidgetItem(tree, QStringList{g["title"_L1].toString()});
        top->setFont(0, bold);
        top->setFirstColumnSpanned(true);
        if (kind == SAME_ITEM)
            top->setForeground(0, QColor(tok("warn")));
        const bool hasKeep = !g["keep"_L1].isNull();
        const qint64 keep = g["keep"_L1].toInteger();
        const QJsonObject overlap = g["overlap"_L1].toObject();
        QList<ItemPtr> items = itemsFrom(g["items"_L1]);
        std::stable_sort(items.begin(), items.end(), [&](const ItemPtr& a, const ItemPtr& b) {
            const bool ka = hasKeep && a->id == keep, kb = hasKeep && b->id == keep;
            return ka && !kb;
        });
        for (const ItemPtr& it : items) {
            const QString folder = rsplitHead(it->relPath);
            QString overlapText;
            if (kind == SAME_ITEM) {
                const QJsonArray o = overlap.value(QString::number(it->id)).toArray();
                const int shared = o.size() >= 2 ? o[0].toInt() : 0, total = o.size() >= 2 ? o[1].toInt() : 0;
                overlapText = u"전체 %1개 중 %2개"_s.arg(total).arg(shared) + (shared == total ? u" (전부)"_s : QString());
            } else {
                overlapText = u"파일 전체"_s;
            }
            auto* ch = new QTreeWidgetItem(top, QStringList{it->name, folder, overlapText, humanSize(it->size), pyDate(it->mtime), statusLabel(it->status)});
            ch->setIcon(0, itemIcon(*it));
            ch->setToolTip(0, it->name);
            ch->setToolTip(1, it->relPath);
            ch->setData(0, Qt::UserRole, QVariant::fromValue(it));
            ch->setFlags(ch->flags() | Qt::ItemIsUserCheckable);
            ch->setCheckState(0, (kind == SAME_FILE && !(hasKeep && it->id == keep)) ? Qt::Checked : Qt::Unchecked);
        }
        top->setExpanded(true);
    }
    summary->setText(groups.isEmpty() ? u"중복이 없습니다."_s
                                      : u"완전히 같은 파일 %1묶음 · 같은 아이템 %2묶음"_s.arg(same).arg(groups.size() - same));
    setButtons(!groups.isEmpty());
}

QList<ItemPtr> DuplicatesPage::checked() const {
    QList<ItemPtr> out;
    for (int i = 0; i < tree->topLevelItemCount(); ++i) {
        QTreeWidgetItem* top = tree->topLevelItem(i);
        for (int j = 0; j < top->childCount(); ++j) {
            QTreeWidgetItem* ch = top->child(j);
            if (ch->checkState(0) == Qt::Checked)
                out << ch->data(0, Qt::UserRole).value<ItemPtr>();
        }
    }
    return out;
}

void DuplicatesPage::apply(const QString& action) {
    const QList<ItemPtr> items = checked();
    if (items.isEmpty())
        return;
    for (int i = 0; i < tree->topLevelItemCount(); ++i) {
        QTreeWidgetItem* top = tree->topLevelItem(i);
        bool all = top->childCount() > 0;
        for (int j = 0; j < top->childCount(); ++j)
            all = all && top->child(j)->checkState(0) == Qt::Checked;
        if (all) {
            QMessageBox::warning(this, u"중복 검사"_s, u"'%1' 묶음이 전부 체크되어 있습니다.\n하나는 남겨두세요."_s.arg(top->text(0)));
            return;
        }
    }
    QStringList lines;
    for (const auto& i : items.mid(0, 10))
        lines << u"· "_s + i->name;
    const QString names = lines.join(u'\n') + (items.size() > 10 ? u"\n…"_s : QString());
    if (action == u"trash"_s) {
        if (!confirm(this, u"중복 정리"_s, u"%1개를 휴지통으로 보낼까요?\n\n%2"_s.arg(items.size()).arg(names)))
            return;
        const QJsonObject res = coreCall("lib.trash", {{"ids"_L1, idsOf(items)}}).toObject();
        QStringList failed;
        for (const auto& f : res["failed"_L1].toArray())
            failed << f.toString();
        if (!failed.isEmpty())
            QMessageBox::warning(this, u"중복 정리"_s,
                                 u"휴지통으로 보내지 못한 항목이 있습니다. 게임이 켜져 있으면 끈 뒤 다시 해 보세요.\n\n"_s + failed.mid(0, 10).join(u'\n'));
    } else {
        if (!confirm(this, u"중복 정리"_s, u"%1개를 비활성화할까요? (Mods 밖으로 옮김)\n\n%2"_s.arg(items.size()).arg(names)))
            return;
        QList<ItemPtr> active;
        for (const auto& i : items)
            if (i->status == u"active"_s)
                active << i;
        coreCall("lib.set_status", {{"ids"_L1, idsOf(active)}, {"to"_L1, u"disabled"_s}, {"label"_L1, u"중복 정리 (%1개)"_s.arg(items.size())}});
    }
    if (changed)
        changed();
    run();
}

// ---------------------------------------------------------------- SettingsPage

SettingsPage::SettingsPage() {
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(u"설정"_s));
    auto* form = new QFormLayout;
    sims = new QLineEdit;
    sims->setReadOnly(true);
    auto* r1 = new QHBoxLayout;
    r1->addWidget(sims, 1);
    auto* b = new QPushButton(u"변경 / 다시 찾기…"_s);
    connect(b, &QPushButton::clicked, this, [this] { changeSims(); });
    r1->addWidget(b);
    form->addRow(u"심즈4 모드 폴더"_s, r1);
    exe = new QLineEdit;
    connect(exe, &QLineEdit::editingFinished, this, [this] { save(); });
    auto* r2 = new QHBoxLayout;
    r2->addWidget(exe, 1);
    auto* b2 = new QPushButton(u"찾아보기…"_s);
    connect(b2, &QPushButton::clicked, this, [this] { browseExe(); });
    auto* b3 = new QPushButton(u"자동 찾기"_s);
    connect(b3, &QPushButton::clicked, this, [this] { autoExe(); });
    r2->addWidget(b2);
    r2->addWidget(b3);
    form->addRow(u"게임 실행 파일"_s, r2);
    exeStatusLabel = new QLabel;
    exeStatusLabel->setObjectName(u"muted"_s);
    form->addRow(QString(), exeStatusLabel);
    chkCache = new QCheckBox(u"게임 실행 전에 썸네일 캐시 비우기"_s);
    connect(chkCache, &QCheckBox::toggled, this, [this] { save(); });
    form->addRow(QString(), chkCache);
    auto* cacheInfo = new QLabel(u"게임이 CC 아이콘을 저장해 두는 파일(localthumbcache.package)입니다. CC를 지우거나 바꾼 뒤 "
                                 u"게임 안에서 아이콘이 깨지거나 예전 모습으로 보일 때 켜두면, 게임이 아이콘을 새로 만듭니다. "
                                 u"지운 파일은 휴지통으로 갑니다."_s);
    cacheInfo->setObjectName(u"muted"_s);
    cacheInfo->setWordWrap(true);
    form->addRow(QString(), cacheInfo);
    theme = new QComboBox;
    theme->addItem(u"자동"_s, u"system"_s);
    theme->setToolTip(u"자동: 윈도우가 다크 모드면 다크, 라이트 모드면 라이트로 바뀝니다."_s);
    theme->addItem(u"다크"_s, u"dark"_s);
    theme->addItem(u"라이트"_s, u"light"_s);
    theme->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    theme->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
    connect(theme, &QComboBox::currentIndexChanged, this, [this] { themePicked(); });
    form->addRow(u"테마"_s, theme);
    lay->addLayout(form);
    lay->addSpacing(18);
    auto* bData = new QPushButton(u"프로그램 설정 파일 폴더 열기"_s);
    bData->setToolTip(core().appDir);
    connect(bData, &QPushButton::clicked, this, [] { QDesktopServices::openUrl(QUrl::fromLocalFile(core().appDir)); });
    lay->addWidget(bData, 0, Qt::AlignLeft);

    lay->addSpacing(18);
    auto* undoBox = new QGroupBox(u"자동 정리 되돌리기"_s);
    auto* ul = new QVBoxLayout(undoBox);
    auto* undoInfo = new QLabel(u"자동 정리로 옮긴 CC를 모두 원래 있던 폴더로 되돌립니다. "
                                u"자동 정리 뒤에 따로 비활성화하거나 지운 파일은 지금 있는 그대로 둡니다."_s);
    undoInfo->setObjectName(u"muted"_s);
    undoInfo->setWordWrap(true);
    ul->addWidget(undoInfo);
    undoStatusLabel = new QLabel;
    ul->addWidget(undoStatusLabel);
    bUndo = new QPushButton(u"자동 정리 되돌리기"_s);
    connect(bUndo, &QPushButton::clicked, this, [this] { undoOrganize(); });
    ul->addWidget(bUndo, 0, Qt::AlignLeft);
    lay->addWidget(undoBox);
    lay->addStretch(1);
    if (usesNanum()) {
        auto* credit = new QLabel(u"이 프로그램에는 네이버에서 제공한 나눔글꼴을 사용하고 있습니다."_s);
        credit->setObjectName(u"muted"_s);
        lay->addWidget(credit);
    }
}

void SettingsPage::refreshPage() {
    const Config& c = cfg();
    sims->setText(c.modsDir);
    exe->setText(c.gameExe);
    chkCache->setChecked(c.clearThumbcacheOnLaunch);
    theme->blockSignals(true);
    theme->setCurrentIndex(qMax(0, theme->findData(cfg().theme)));
    theme->blockSignals(false);
    exeStatus();
    undoStatus();
}

void SettingsPage::undoStatus() {
    const QJsonArray h = coreCall("lib.organize_history").toArray();
    const int runs = h[0].toInt(), files = h[1].toInt();
    undoStatusLabel->setText(runs ? u"되돌릴 수 있는 자동 정리 %1번 · 파일 %2개"_s.arg(runs).arg(files)
                                  : u"되돌릴 자동 정리가 없습니다."_s);
    bUndo->setEnabled(runs != 0);
}

void SettingsPage::undoOrganize() {
    const QJsonArray h = coreCall("lib.organize_history").toArray();
    const int runs = h[0].toInt(), files = h[1].toInt();
    if (!runs || !confirm(this, u"자동 정리 되돌리기"_s, u"자동 정리 %1번으로 옮긴 파일 %2개를 원래 폴더로 되돌릴까요?"_s.arg(runs).arg(files)))
        return;
    int n = 0;
    try {
        n = runWithLoading(this, u"자동 정리 되돌리는 중"_s, [](const LoadProgress& progress) {
            const QJsonValue moved = coreCall("lib.undo_organize", {}, phased(progress, u"파일을 원래 폴더로 옮기는 중"_s));
            coreCall("lib.scan", {}, phased(progress, u"목록에 반영하는 중"_s));
            return moved;
        }, Quips::Organize).toInt();
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"자동 정리 되돌리기"_s, u"되돌리는 중 오류: "_s + ex.msg);
        n = 0;
    }
    if (changed)
        changed();
    undoStatus();
    QMessageBox::information(this, u"자동 정리 되돌리기"_s, u"%1개 파일을 원래 폴더로 되돌렸습니다."_s.arg(n));
}

void SettingsPage::exeStatus() {
    const QString p = exe->text();
    exeStatusLabel->setText(isFile(p) ? u"찾음"_s : u"실행 파일이 없으면 [게임 실행] 버튼만 쓸 수 없습니다."_s);
}

void SettingsPage::changeSims() {
    SetupDialog d(this, cfg().modsDir);
    if (d.exec() && !d.resultDir.isEmpty()) {
        setConfig({{"sims_dir"_L1, d.resultDir}}, true);
        sims->setText(cfg().modsDir);
        if (simsDirChanged)
            simsDirChanged();
    }
}

void SettingsPage::browseExe() {
    const QString p = QFileDialog::getOpenFileName(this, u"TS4_x64.exe 선택"_s, exe->text(), u"TS4_x64.exe (TS4_x64.exe);;실행 파일 (*.exe)"_s);
    if (!p.isEmpty()) {
        exe->setText(QDir::toNativeSeparators(QDir::cleanPath(p)));
        save();
    }
}

void SettingsPage::autoExe() {
    const QString p = coreCall("paths.find_game_exe").toString();
    if (!p.isEmpty())
        exe->setText(p);
    else
        QMessageBox::information(this, u"게임 실행 파일"_s, u"자동으로 찾지 못했습니다. [찾아보기]로 TS4_x64.exe를 지정하세요.\n"
                                                           u"(보통 ...\\The Sims 4\\Game\\Bin\\TS4_x64.exe)"_s);
    save();
}

void SettingsPage::save() {
    setConfig({{"game_exe"_L1, exe->text().trimmed()}, {"clear_thumbcache_on_launch"_L1, chkCache->isChecked()}}, true);
    exeStatus();
}

void SettingsPage::themePicked() {
    const QString t = theme->currentData().toString();
    if (t != cfg().theme) {
        setConfig({{"theme"_L1, t}}, true);
        if (themeChanged)
            themeChanged(t);
    }
}
