#include "tray_page.h"

constexpr int EntryRole = Qt::UserRole + 1;
constexpr int TYPE_HOUSEHOLD = 1, TYPE_LOT = 2, TYPE_ROOM = 3;

static QStringList strings(const QJsonValue& v) {
    QStringList out;
    for (const auto& x : v.toArray())
        out << x.toString();
    return out;
}

static TrayEntryPtr entryFrom(const QJsonObject& o) {
    auto e = std::make_shared<TrayEntry>();
    e->key = o["key"_L1].toString();
    e->kind = o["kind"_L1].toInt();
    e->kindLabel = o["kind_label"_L1].toString();
    e->name = o["name"_L1].toString();
    e->creator = o["creator"_L1].toString();
    e->description = o["description"_L1].toString();
    e->savedAt = o["saved_at"_L1].toString();
    e->thumb = o["thumb"_L1].toString();
    e->exportName = o["export_name"_L1].toString();
    e->files = o["files"_L1].toInt();
    e->hasMainImages = o["has_main_images"_L1].toBool();
    for (const auto& sv : o["sims"_L1].toArray()) {
        const QJsonObject so = sv.toObject();
        TraySim sim;
        sim.fullName = so["full_name"_L1].toString();
        sim.summary = so["summary"_L1].toString();
        sim.aspiration = so["aspiration"_L1].toString();
        sim.traits = strings(so["traits"_L1]);
        sim.likes = strings(so["likes"_L1]);
        sim.dislikes = strings(so["dislikes"_L1]);
        for (const auto& kv : so["skills"_L1].toArray()) {
            const QJsonObject ko = kv.toObject();
            sim.skills.append({ko["name"_L1].toString(), ko["level"_L1].toInt(), ko["max_level"_L1].toInt()});
        }
        if (so["relations"_L1].isObject()) {
            const QJsonObject ro = so["relations"_L1].toObject();
            sim.hasRelations = true;
            sim.relSource = ro["source"_L1].toString();
            for (const auto& rv : ro["rels"_L1].toArray()) {
                const QJsonArray r = rv.toArray();
                TrayRelation rel;
                rel.name = r[0].toString();
                rel.hasFriendship = !r[1].isNull();
                rel.friendship = r[1].toDouble();
                rel.hasRomance = !r[2].isNull();
                rel.romance = r[2].toDouble();
                sim.relations << rel;
            }
        }
        e->sims << sim;
    }
    return e;
}

// ---------------------------------------------------------------- SkillBars

SkillBars::SkillBars(const QList<TraySkill>& skills) : skills_(skills) {
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    setFixedHeight(rows() * ROW_H + 4);
}

QSize SkillBars::sizeHint() const {
    return QSize(COLS * colWidth(), rows() * ROW_H + 4);
}

void SkillBars::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    const int nrows = rows();
    const int colW = qMax(colWidth(), width() / COLS);
    const QFontMetrics fm = p.fontMetrics();
    for (int i = 0; i < skills_.size(); ++i) {
        const TraySkill& sk = skills_[i];
        const int col = nrows ? i / nrows : 0, row = nrows ? i % nrows : 0;
        const int x0 = col * colW, y0 = row * ROW_H + 2;
        const QString name = fm.elidedText(sk.name, Qt::ElideRight, NAME_W);
        p.setPen(QColor(tok("text")));
        p.drawText(QRectF(x0, y0, NAME_W, ROW_H - 4), Qt::AlignRight | Qt::AlignVCenter, name);
        const int cells = sk.maxLevel ? sk.maxLevel : 10;
        for (int c = 0; c < cells; ++c) {
            const int x = x0 + NAME_W + 8 + c * (CELL + GAP);
            const QRectF rect(x, y0 + (ROW_H - 4 - 12) / 2.0, CELL, 12);
            p.setPen(Qt::NoPen);
            p.setBrush(QColor(c < sk.level ? tok("accent") : tok("track")));
            p.drawRoundedRect(rect, 2, 2);
        }
    }
    p.end();
}

// ---------------------------------------------------------------- TrayPage

TrayPage::TrayPage() {
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(u"트레이"_s,
                             u"게임 라이브러리에 저장해 둔 가족, 집, 방을 보여 줍니다. "
                             u"항목을 고르면 그 가족이나 집에 쓰인 CC를 확인할 수 있습니다."_s, true));

    auto* bar = new QHBoxLayout;
    kind = new QComboBox;
    kind->addItem(u"전체"_s, 0);
    kind->addItem(u"가족"_s, TYPE_HOUSEHOLD);
    kind->addItem(u"집"_s, TYPE_LOT);
    kind->addItem(u"방"_s, TYPE_ROOM);
    kind->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    connect(kind, &QComboBox::currentIndexChanged, this, [this] { fill(); });
    search = new QLineEdit;
    search->setPlaceholderText(u"이름, 제작자, 심 이름 검색"_s);
    search->setFixedWidth(260);
    connect(search, &QLineEdit::textChanged, this, [this] { fill(); });
    count = mutedLabel();
    auto* bOpen = new QPushButton(u"Tray 폴더 열기"_s);
    connect(bOpen, &QPushButton::clicked, this, [this] { reveal(trayDir()); });
    auto* bReload = new QPushButton(u"새로고침"_s);
    connect(bReload, &QPushButton::clicked, this, [this] { refresh(true); });
    bar->addWidget(kind);
    bar->addWidget(search);
    bar->addWidget(count, 1);
    bar->addWidget(bOpen);
    bar->addWidget(bReload);
    lay->addLayout(bar);

    list = new QListWidget;
    list->setObjectName(u"grid"_s);
    list->setViewMode(QListView::IconMode);
    list->setIconSize(QSize(180, 120));
    list->setGridSize(QSize(208, 168));
    list->setResizeMode(QListView::Adjust);
    list->setMovement(QListView::Static);
    list->setWordWrap(true);
    list->setUniformItemSizes(true);
    list->setSelectionMode(QAbstractItemView::SingleSelection);
    connect(list, &QListWidget::currentItemChanged, this, [this](QListWidgetItem* cur, QListWidgetItem*) {
        TrayEntryPtr e;
        if (cur) {
            const QString key = cur->data(EntryRole).toString();
            for (const auto& x : entries)
                if (x->key == key)
                    e = x;
        }
        show(e);
    });

    detail = new QWidget;
    auto* dl = new QVBoxLayout(detail);
    dl->setContentsMargins(14, 14, 14, 14);
    dl->setSpacing(8);
    image = new QLabel;
    image->setAlignment(Qt::AlignCenter);
    image->setMinimumHeight(200);
    name = new QLabel;
    name->setObjectName(u"h1"_s);
    name->setWordWrap(true);
    sub = mutedLabel();
    desc = new QLabel;
    desc->setWordWrap(true);
    actions = new QWidget;
    auto* al = new QHBoxLayout(actions);
    al->setContentsMargins(0, 0, 0, 0);
    bThumb = new QPushButton(u"썸네일 바꾸기…"_s);
    bThumb->setToolTip(u"게임 라이브러리에 보이는 대표 사진을 바꿉니다.\n[사본으로 저장]을 눌러야 저장됩니다."_s);
    connect(bThumb, &QPushButton::clicked, this, [this] { replaceThumb(); });
    bClean = new QPushButton(u"외형만 남기기"_s);
    bClean->setToolTip(u"지우는 것:%1\n남기는 것: %2\n[사본으로 저장]을 눌러야 저장됩니다."_s
                           .arg(core().clearedLabels.join(u", "_s), core().keptLabels.join(u", "_s)));
    connect(bClean, &QPushButton::clicked, this, [this] { lookOnly(); });
    bExport = new QPushButton(u"내보내기…"_s);
    bExport->setToolTip(u"트레이 파일을 zip으로 묶습니다. 사용한 CC를 함께 넣을 수 있습니다."_s);
    connect(bExport, &QPushButton::clicked, this, [this] { exportEntry(); });
    bRename = new QPushButton(u"이름 바꾸기…"_s);
    bRename->setToolTip(u"가족 이름이나 부지 이름을 바꿉니다.\n[사본으로 저장]을 눌러야 저장됩니다."_s);
    connect(bRename, &QPushButton::clicked, this, [this] { rename(); });
    bDesc = new QPushButton(u"설명 바꾸기…"_s);
    bDesc->setToolTip(u"게임 라이브러리에 보이는 설명을 바꿉니다.\n[사본으로 저장]을 눌러야 저장됩니다."_s);
    connect(bDesc, &QPushButton::clicked, this, [this] { editDesc(); });
    bDelete = new QPushButton(u"지우기"_s);
    bDelete->setObjectName(u"danger"_s);
    bDelete->setToolTip(u"이 항목의 트레이 파일을 모두 휴지통으로 보냅니다."_s);
    connect(bDelete, &QPushButton::clicked, this, [this] { deleteEntry(); });
    al->addWidget(bThumb);
    al->addWidget(bRename);
    al->addWidget(bDesc);
    al->addWidget(bClean);
    al->addWidget(bExport);
    al->addStretch(1);
    al->addWidget(bDelete);
    pendingBar = new QFrame;
    pendingBar->setObjectName(u"card"_s);
    auto* pl = new QHBoxLayout(pendingBar);
    pl->setContentsMargins(10, 8, 10, 8);
    pendingLabel = new QLabel;
    pendingLabel->setWordWrap(true);
    bSave = new QPushButton(u"사본으로 저장"_s);
    bSave->setObjectName(u"primary"_s);
    bSave->setToolTip(u"원본은 그대로 두고, 바꾼 내용을 새 트레이 항목으로 저장합니다."_s);
    connect(bSave, &QPushButton::clicked, this, [this] { saveCopy(); });
    bDiscard = new QPushButton(u"변경 취소"_s);
    connect(bDiscard, &QPushButton::clicked, this, [this] { discard(); });
    pl->addWidget(pendingLabel, 1);
    pl->addWidget(bDiscard);
    pl->addWidget(bSave);
    simsTitle = new QLabel;
    simsTitle->setObjectName(u"section"_s);
    simsBox = new QVBoxLayout;
    simsBox->setSpacing(10);
    ccTitle = new QLabel;
    ccTitle->setObjectName(u"section"_s);
    cc = new QTreeWidget;
    cc->setHeaderLabels({u"이름"_s, u"분류"_s, u"상태"_s});
    cc->setRootIsDecorated(false);
    cc->setIconSize(QSize(32, 32));
    cc->header()->setSectionResizeMode(QHeaderView::Interactive);
    cc->header()->setStretchLastSection(true);
    cc->setColumnWidth(0, 300);
    cc->setColumnWidth(1, 150);
    cc->setMinimumHeight(280);
    cc->setEditTriggers(QAbstractItemView::NoEditTriggers);
    connect(cc, &QTreeWidget::itemDoubleClicked, this, [](QTreeWidgetItem* row, int) {
        const ItemPtr it = row->data(0, Qt::UserRole).value<ItemPtr>();
        if (it)
            reveal(libAbs(it->relPath));
    });
    ccHint = mutedLabel(u"더블 클릭하면 CC 파일이 있는 폴더를 엽니다. 내 Mods에 없는 CC나 게임 기본 아이템은 목록에 나오지 않습니다."_s);
    for (QWidget* w : std::initializer_list<QWidget*>{image, name, sub, actions, pendingBar, desc, simsTitle})
        dl->addWidget(w);
    dl->addLayout(simsBox);
    dl->addWidget(ccTitle);
    dl->addWidget(ccHint);
    dl->addWidget(cc, 1);
    scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setWidget(detail);
    scroll->setMinimumWidth(420);

    auto* split = new QSplitter;
    split->addWidget(list);
    split->addWidget(scroll);
    split->setStretchFactor(0, 1);
    split->setStretchFactor(1, 1);
    split->setSizes({520, 560});
    lay->addWidget(split, 1);
    show(nullptr);
}

QString TrayPage::trayDir() const {
    const QString sims = cfg().simsDir;
    if (sims.isEmpty())
        return u"Tray"_s;
    return (sims.endsWith(u'\\') || sims.endsWith(u'/')) ? sims + u"Tray"_s : sims + u"\\Tray"_s;
}

TrayEntryPtr TrayPage::currentEntry() const {
    QListWidgetItem* cur = list->currentItem();
    if (!cur)
        return nullptr;
    const QString key = cur->data(EntryRole).toString();
    for (const auto& x : entries)
        if (x->key == key)
            return x;
    return nullptr;
}

void TrayPage::refresh(bool force) {
    const QString dir = trayDir();
    if (!force && loaded_ && dir == loadedDir_) {
        coreCall("tray.refresh_idmap");
        // 같은 항목을 다시 그리는 것이므로 오른쪽 정보와 사용한 CC 목록의 스크롤 위치를 그대로 둔다.
        QScrollBar* outer = scroll->verticalScrollBar();
        QScrollBar* inner = cc->verticalScrollBar();
        const int outerPos = outer->value(), innerPos = inner->value();
        show(currentEntry());
        detail->layout()->activate();
        outer->setValue(outerPos);
        inner->setValue(innerPos);
        // 레이아웃이 한 번 더 계산된 뒤에도 같은 위치가 되도록 다시 맞춘다.
        QPointer<QScrollBar> o(outer), i(inner);
        QTimer::singleShot(0, this, [o, i, outerPos, innerPos] {
            if (o)
                o->setValue(outerPos);
            if (i)
                i->setValue(innerPos);
        });
        return;
    }
    if (!isDir(dir)) {
        entries.clear();
        coreCall("tray.clear");
        loaded_ = true;
        loadedDir_ = dir;
        fill();
        return;
    }
    try {
        const QJsonValue res = runWithLoading(this, u"트레이를 읽는 중"_s, [dir](const LoadProgress& progress) {
            return coreCall("tray.load", {{"tray_dir"_L1, dir}}, phased(progress, u"미리보기 그림을 준비하는 중"_s));
        }, Quips::Tray);
        entries.clear();
        for (const auto& v : res.toArray())
            entries << entryFrom(v.toObject());
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"트레이"_s, u"트레이를 읽는 중 오류: "_s + ex.msg);
        entries.clear();
    }
    loaded_ = true;
    loadedDir_ = dir;
    fill();
}

void TrayPage::fill() {
    const int kindValue = kind->currentData().toInt();
    const QStringList terms = search->text().trimmed().toLower().split(QRegularExpression(u"\\s+"_s), Qt::SkipEmptyParts);
    QString currentKey = list->currentItem() ? list->currentItem()->data(EntryRole).toString() : QString();
    if (!selectKey_.isEmpty()) {
        currentKey = selectKey_;
        selectKey_.clear();
    }
    list->clear();
    int shown = 0;
    for (const auto& e : entries) {
        if (kindValue && e->kind != kindValue)
            continue;
        QStringList parts{e->name, e->creator, e->description};
        for (const auto& s : e->sims)
            parts << s.fullName;
        const QString hay = parts.join(u' ').toLower();
        bool ok = true;
        for (const QString& t : terms)
            ok = ok && hay.contains(t);
        if (!ok)
            continue;
        const QString thumb = thumbOf(*e);
        const QPixmap pm = thumb.isEmpty() ? placeholder(QString(), 120) : QPixmap(thumb);
        const QString label = e->name + u'\n' + e->kindLabel + (pending.contains(e->key) ? u" · 저장 안 됨"_s : QString());
        auto* it = new QListWidgetItem(QIcon(pm.scaled(180, 120, Qt::KeepAspectRatio, Qt::SmoothTransformation)), label);
        it->setData(EntryRole, e->key);
        it->setToolTip(e->name + u'\n' + e->kindLabel + u" · "_s + e->creator);
        list->addItem(it);
        if (!currentKey.isEmpty() && e->key == currentKey)
            list->setCurrentItem(it);
        ++shown;
    }
    if (!isDir(trayDir()))
        count->setText(u"Tray 폴더가 없습니다."_s);
    else
        count->setText(u"%1개"_s.arg(shown) + (shown != entries.size() ? u" / 전체 %1개"_s.arg(entries.size()) : QString()));
    if (!list->currentItem())
        show(nullptr);
}

void TrayPage::clearSims() {
    while (simsBox->count()) {
        QLayoutItem* item = simsBox->takeAt(0);
        if (QWidget* w = item->widget())
            w->deleteLater();
        delete item;
    }
}

void TrayPage::show(const TrayEntryPtr& e) {
    clearSims();
    cc->clear();
    const bool has = e != nullptr;
    current = e;
    for (QWidget* w : std::initializer_list<QWidget*>{simsTitle, ccTitle, cc, ccHint, desc, actions})
        w->setVisible(has);
    pendingBar->hide();
    if (!has) {
        image->setPixmap(placeholder(QString(), 160));
        name->setText(u"항목을 선택하세요"_s);
        sub->setText(u"왼쪽에서 가족, 집, 방을 고르면 자세한 정보와 사용한 CC가 표시됩니다."_s);
        simsTitle->hide();
        return;
    }
    const Pending pend = pending.value(e->key);
    const QString thumb = thumbOf(*e);
    const QPixmap pm = thumb.isEmpty() ? placeholder(QString(), 160) : QPixmap(thumb);
    image->setPixmap(pm.scaled(420, 280, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    QStringList lines, changedBits;
    if (pend.hasName) changedBits << u"이름"_s;
    if (pend.hasThumb) changedBits << u"썸네일"_s;
    if (pend.hasDesc) changedBits << u"설명"_s;
    if (!changedBits.isEmpty())
        lines << u"<b>바뀌는 것</b>&nbsp;&nbsp;"_s + changedBits.join(u" · "_s);
    if (pend.clean)
        lines << u"<b>지워지는 것</b>&nbsp;&nbsp;"_s + core().clearedLabels.join(u", "_s).toHtmlEscaped();
    pendingBar->setVisible(!lines.isEmpty());
    pendingLabel->setText(lines.join(u"<br>"_s));
    name->setText(!pend.name.isEmpty() ? pend.name : !e->name.isEmpty() ? e->name : u"(이름 없음)"_s);
    QStringList bits{e->kindLabel};
    if (!e->creator.isEmpty())
        bits << u"제작: "_s + e->creator;
    if (!e->savedAt.isEmpty())
        bits << e->savedAt;
    sub->setText(bits.join(u" · "_s));
    const QString description = pend.hasDesc ? pend.desc : e->description;
    desc->setText(description);
    desc->setVisible(!description.isEmpty());

    simsTitle->setVisible(!e->sims.isEmpty());
    simsTitle->setText(u"구성원 %1명"_s.arg(e->sims.size()));
    const QJsonArray portraits = coreCall("tray.sim_thumbs", {{"key"_L1, e->key}}).toArray();
    for (int i = 0; i < e->sims.size(); ++i)
        simsBox->addWidget(simCard(e->sims[i], i < portraits.size() ? portraits[i].toString() : QString(), pend.clean));

    bThumb->setEnabled(e->hasMainImages);
    bClean->setVisible(e->kind == TYPE_HOUSEHOLD);
    bClean->setEnabled(!e->sims.isEmpty() && !pend.clean);
    const QList<ItemPtr> used = itemsFrom(coreCall("tray.used_cc", {{"key"_L1, e->key}}));
    usedCcCount_ = int(used.size());
    ccTitle->setText(u"사용한 CC %1개"_s.arg(used.size()));
    for (const ItemPtr& it : used) {
        auto* row = new QTreeWidgetItem(QStringList{it->name, categoryPathLabel(it->category), statusLabel(it->status)});
        row->setIcon(0, itemIcon(*it));
        row->setData(0, Qt::UserRole, QVariant::fromValue(it));
        row->setToolTip(0, it->relPath);
        cc->addTopLevelItem(row);
    }
}

static QLabel* richLabel(const QString& html) {
    auto* text = new QLabel(html);
    text->setTextFormat(Qt::RichText);
    return text;
}

QWidget* TrayPage::simCard(const TraySim& sim, const QString& portrait, bool cleaned) {
    auto* card = new QFrame;
    card->setObjectName(u"card"_s);
    auto* grid = new QGridLayout(card);
    grid->setContentsMargins(10, 10, 10, 10);
    grid->setHorizontalSpacing(12);
    auto* pic = new QLabel;
    const QPixmap pm = portrait.isEmpty() ? placeholder(QString(), 72) : QPixmap(portrait);
    pic->setPixmap(pm.scaled(72, 72, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    grid->addWidget(pic, 0, 0, 5, 1, Qt::AlignTop);
    auto* nameLabel = new QLabel(sim.fullName.isEmpty() ? u"(이름 없음)"_s : sim.fullName);
    nameLabel->setObjectName(u"h2"_s);
    grid->addWidget(nameLabel, 0, 1);
    int row = 1;
    const QList<std::pair<QString, QString>> facts = {
        {QString(), sim.summary}, {u"특성"_s, sim.traits.join(u", "_s)}, {u"열망"_s, sim.aspiration},
        {u"좋아함"_s, sim.likes.join(u", "_s)}, {u"싫어함"_s, sim.dislikes.join(u", "_s)}};
    for (const auto& [label, value] : facts) {
        if (value.isEmpty())
            continue;
        auto* text = richLabel(label.isEmpty() ? value.toHtmlEscaped() : u"<b>%1</b>&nbsp;&nbsp;%2"_s.arg(label, value.toHtmlEscaped()));
        text->setWordWrap(true);
        if (label.isEmpty())
            text->setObjectName(u"muted"_s);
        grid->addWidget(text, row++, 1);
    }
    QList<TraySkill> known;
    int unnamed = 0;
    for (const auto& sk : sim.skills) {
        if (!sk.name.isEmpty())
            known << sk;
        else
            ++unnamed;
    }
    if (cleaned) {
        grid->addWidget(richLabel(u"<b>스킬</b>&nbsp;&nbsp;지워짐"_s), row++, 1);
        known.clear();
    }
    if (!known.isEmpty()) {
        grid->addWidget(new QLabel(u"<b>스킬</b>"_s), row++, 1);
        grid->addWidget(new SkillBars(known), row++, 1);
    }
    if (!cleaned && unnamed)
        grid->addWidget(mutedLabel(u"표에 없는 스킬 %1개 (모드 스킬 등)"_s.arg(unnamed)), row++, 1);
    grid->setColumnStretch(1, 1);
    return card;
}

QString TrayPage::thumbOf(const TrayEntry& e) const {
    const Pending p = pending.value(e.key);
    return !p.thumb.isEmpty() ? p.thumb : e.thumb;
}

void TrayPage::staged(const TrayEntryPtr& e) {
    fill();
    show(e);
}

void TrayPage::replaceThumb() {
    const TrayEntryPtr e = current;
    if (!e)
        return;
    const QString path = QFileDialog::getOpenFileName(this, u"새 썸네일 이미지 선택"_s, QString(), u"이미지 (*.png *.jpg *.jpeg *.webp *.bmp)"_s);
    if (path.isEmpty())
        return;
    if (QPixmap(path).isNull()) {
        QMessageBox::warning(this, u"썸네일 바꾸기"_s, u"이 이미지는 열 수 없습니다."_s);
        return;
    }
    Pending& p = pending[e->key];
    p.hasThumb = true;
    p.thumb = path;
    staged(e);
}

void TrayPage::lookOnly() {
    const TrayEntryPtr e = current;
    if (e) {
        pending[e->key].clean = true;
        staged(e);
    }
}

void TrayPage::discard() {
    const TrayEntryPtr e = current;
    if (e && pending.remove(e->key)) {
        fill();
        show(e);
    }
}

void TrayPage::stageText(const TrayEntryPtr& e, bool isName, const QString& text, const QString& original) {
    if (text == original) {
        auto found = pending.find(e->key);
        if (found != pending.end() && (isName ? found->hasName : found->hasDesc)) {
            if (isName) {
                found->hasName = false;
                found->name.clear();
            } else {
                found->hasDesc = false;
                found->desc.clear();
            }
            if (found->empty())
                pending.erase(found);
            fill();
            show(e);
        }
        return;
    }
    Pending& p = pending[e->key];
    if (isName) {
        p.hasName = true;
        p.name = text;
    } else {
        p.hasDesc = true;
        p.desc = text;
    }
    staged(e);
}

void TrayPage::rename() {
    const TrayEntryPtr e = current;
    if (!e)
        return;
    const Pending pend = pending.value(e->key);
    bool ok = false;
    QString text = QInputDialog::getText(this, u"이름 바꾸기"_s, u"새 이름:"_s, QLineEdit::Normal,
                                         !pend.name.isEmpty() ? pend.name : e->name, &ok);
    if (!ok)
        return;
    text = text.trimmed();
    if (text.isEmpty()) {
        QMessageBox::information(this, u"이름 바꾸기"_s, u"이름을 비워 둘 수는 없습니다."_s);
        return;
    }
    stageText(e, true, text, e->name);
}

void TrayPage::editDesc() {
    const TrayEntryPtr e = current;
    if (!e)
        return;
    const Pending pend = pending.value(e->key);
    bool ok = false;
    const QString text = QInputDialog::getMultiLineText(this, u"설명 바꾸기"_s, u"새 설명:"_s,
                                                        pend.hasDesc ? pend.desc : e->description, &ok);
    if (ok)
        stageText(e, false, text.trimmed(), e->description);
}

void TrayPage::deleteEntry() {
    const TrayEntryPtr e = current;
    if (!e)
        return;
    QMessageBox box(this);
    box.setWindowTitle(u"지우기"_s);
    box.setIcon(QMessageBox::Warning);
    box.setText(u"'%1' (%2)을(를) 지울까요?"_s.arg(e->name.isEmpty() ? e->key : e->name, e->kindLabel));
    box.setInformativeText(u"트레이 파일 %1개를 휴지통으로 보냅니다. 휴지통에서 되살릴 수 있습니다."_s.arg(e->files));
    QAbstractButton* yes = box.addButton(u"휴지통으로 보내기"_s, QMessageBox::AcceptRole);
    box.addButton(u"취소"_s, QMessageBox::RejectRole);
    box.exec();
    if (box.clickedButton() != yes)
        return;
    try {
        coreCall("tray.delete", {{"key"_L1, e->key}});
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"지우기"_s, u"지우지 못했습니다: "_s + ex.msg);
    }
    pending.remove(e->key);
    refresh(true);
}

TrayEntryPtr TrayPage::saveCopy() {
    const TrayEntryPtr e = current;
    if (!e || !pending.contains(e->key) || pending.value(e->key).empty())
        return nullptr;
    const Pending pend = pending.value(e->key);
    const QString newName = !pend.name.isEmpty() ? pend.name : e->name;
    QJsonObject args{{"key"_L1, e->key}, {"tray_dir"_L1, trayDir()},
                     {"name"_L1, newName != e->name ? newName : QString()}, {"clean"_L1, pend.clean}};
    if (!pend.thumb.isEmpty())
        args["thumb"_L1] = pend.thumb;
    if (pend.hasDesc)
        args["desc"_L1] = pend.desc;
    QJsonObject result;
    try {
        result = runWithLoading(this, u"사본으로 저장하는 중"_s, [args](const LoadProgress&) {
            return coreCall("tray.save_copy", args);
        }).toObject();
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"사본으로 저장"_s, u"저장하지 못했습니다. 원본은 바뀌지 않았습니다.\n\n"_s + ex.msg);
        return nullptr;
    }
    const QString newKey = result["key"_L1].toString();
    pending.remove(e->key);
    selectKey_ = newKey;
    refresh(true);
    QStringList done{u"새 항목 '%1'을(를) 만들었습니다. 원본은 그대로 있습니다."_s.arg(newName)};
    if (pend.clean)
        done << u"스킬, 직업 같은 플레이 기록을 지우고 외형만 남겼습니다."_s;
    if (result["thumbs"_L1].toInt())
        done << u"새 썸네일을 넣었습니다."_s;
    if (pend.hasDesc)
        done << u"설명을 바꿨습니다."_s;
    done << u"게임을 다시 켜면 라이브러리에 보입니다."_s;
    QMessageBox::information(this, u"사본으로 저장"_s, done.join(u'\n'));
    for (const auto& x : entries)
        if (x->key == newKey)
            return x;
    return nullptr;
}

void TrayPage::exportEntry() {
    TrayEntryPtr e = current;
    if (!e)
        return;
    if (pending.contains(e->key)) {
        QMessageBox ask(this);
        ask.setWindowTitle(u"내보내기"_s);
        ask.setIcon(QMessageBox::Question);
        ask.setText(u"저장하지 않은 변경이 있습니다."_s);
        ask.setInformativeText(u"바꾼 내용을 사본으로 저장한 뒤 그 사본을 내보낼까요?"_s);
        QPushButton* saveFirst = ask.addButton(u"저장하고 내보내기"_s, QMessageBox::AcceptRole);
        saveFirst->setObjectName(u"primary"_s);
        repolish(saveFirst);
        QAbstractButton* original = ask.addButton(u"원본 내보내기"_s, QMessageBox::AcceptRole);
        ask.addButton(u"취소"_s, QMessageBox::RejectRole);
        ask.exec();
        if (ask.clickedButton() == saveFirst) {
            e = saveCopy();
            if (!e)
                return;
        } else if (ask.clickedButton() != original) {
            return;
        }
    }
    const int ccCount = int(coreCall("tray.used_cc", {{"key"_L1, e->key}}).toArray().size());
    QMessageBox box(this);
    box.setWindowTitle(u"내보내기"_s);
    box.setIcon(QMessageBox::Question);
    box.setText(u"'%1'을(를) zip 파일로 내보냅니다."_s.arg(e->exportName));
    box.setInformativeText(u"사용한 CC %1개를 함께 넣을까요?\n"
                           u"zip 안에서 트레이 파일은 Tray 폴더에, CC는 Mods 폴더에 나눠 담습니다.\n"
                           u"받는 사람은 zip을 S4CCM에 끌어다 놓거나, 두 폴더를 심즈 4 폴더에 넣으면 됩니다."_s.arg(ccCount));
    QPushButton* withCc = box.addButton(u"CC 포함 (%1개)"_s.arg(ccCount), QMessageBox::AcceptRole);
    withCc->setObjectName(u"primary"_s);
    repolish(withCc);
    QAbstractButton* without = box.addButton(u"CC 없이"_s, QMessageBox::AcceptRole);
    box.addButton(u"취소"_s, QMessageBox::RejectRole);
    box.exec();
    QAbstractButton* clicked = box.clickedButton();
    if (clicked != withCc && clicked != without)
        return;
    const QString outDir = QFileDialog::getExistingDirectory(this, u"zip 파일을 저장할 폴더 선택"_s, QDir::homePath() + u"/Desktop"_s);
    if (outDir.isEmpty())
        return;
    QString path;
    const QJsonObject args{{"key"_L1, e->key}, {"out_dir"_L1, outDir}, {"with_cc"_L1, clicked == withCc}};
    try {
        path = runWithLoading(this, u"내보내는 중"_s, [args](const LoadProgress&) { return coreCall("tray.export_zip", args); }).toString();
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"내보내기"_s, u"내보내지 못했습니다: "_s + ex.msg);
        return;
    }
    QMessageBox::information(this, u"내보내기"_s, u"저장했습니다:\n"_s + path);
    reveal(path);
}
