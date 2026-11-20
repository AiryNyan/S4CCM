#include "dialogs.h"

static const QString MODE_SCRIPT = u"script"_s;
static const QString MODE_CC = u"cc"_s;

// ---------------------------------------------------------------- SetupDialog

SetupDialog::SetupDialog(QWidget* parent, const QString& current) : QDialog(parent) {
    setWindowTitle(u"심즈4 모드 폴더 설정"_s);
    setMinimumWidth(620);
    auto* lay = new QVBoxLayout(this);
    auto* title = new QLabel(u"심즈4 모드 폴더 찾기"_s);
    title->setObjectName(u"h1"_s);
    lay->addWidget(title);

    QList<QJsonObject> found;
    for (const auto& f : coreCall("paths.find_sims_folders").toArray())
        found << f.toObject();
    group = new QButtonGroup(this);
    if (!found.isEmpty()) {
        lay->addWidget(new QLabel(found.size() > 1 ? u"모드 폴더를 여러 개 찾았습니다. 사용할 폴더를 고르세요."_s
                                                   : u"심즈4 모드 폴더를 자동으로 찾았습니다."_s));
        std::stable_sort(found.begin(), found.end(), [](const QJsonObject& a, const QJsonObject& b) {
            return a["cc_count"_L1].toInt() > b["cc_count"_L1].toInt();
        });
        int i = 0;
        for (const QJsonObject& f : found) {
            auto* rb = new QRadioButton(f["mods"_L1].toString());
            rb->setProperty("path", f["root"_L1].toString());
            group->addButton(rb, i);
            lay->addWidget(rb);
            if (i == 0)
                rb->setChecked(true);
            ++i;
        }
    } else {
        auto* warn = new QLabel(u"심즈4 모드 폴더를 자동으로 찾지 못했습니다. 아래에서 Mods 폴더를 직접 지정하세요."_s);
        warn->setObjectName(u"warn"_s);
        warn->setWordWrap(true);
        lay->addWidget(warn);
    }

    manualRb = new QRadioButton(u"직접 지정:"_s);
    group->addButton(manualRb, 999);
    auto* row = new QHBoxLayout;
    row->addWidget(manualRb);
    edit = new QLineEdit(current);
    edit->setPlaceholderText(u"예: C:\\Users\\이름\\Documents\\Electronic Arts\\The Sims 4\\Mods"_s);
    connect(edit, &QLineEdit::textEdited, this, [this] { manualRb->setChecked(true); });
    connect(edit, &QLineEdit::textChanged, this, [this] { validate(); });
    row->addWidget(edit, 1);
    auto* b = new QPushButton(u"찾아보기…"_s);
    connect(b, &QPushButton::clicked, this, [this] { browse(); });
    row->addWidget(b);
    lay->addLayout(row);
    if (found.isEmpty())
        manualRb->setChecked(true);
    status = new QLabel;
    status->setWordWrap(true);
    lay->addWidget(status);
    auto* hint = new QLabel(u"팁: 폴더를 이 창에 끌어다 놓아도 됩니다."_s);
    hint->setObjectName(u"muted"_s);
    lay->addWidget(hint);

    buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
    connect(buttons, &QDialogButtonBox::accepted, this, [this] { acceptChoice(); });
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    lay->addWidget(buttons);
    connect(group, &QButtonGroup::buttonToggled, this, [this] { validate(); });
    setAcceptDrops(true);
    validate();
}

void SetupDialog::dragEnterEvent(QDragEnterEvent* e) {
    if (e->mimeData()->hasUrls())
        e->acceptProposedAction();
}

void SetupDialog::dropEvent(QDropEvent* e) {
    const auto urls = e->mimeData()->urls();
    if (urls.isEmpty())
        return;
    const QString p = urls.first().toLocalFile();
    if (isDir(p)) {
        edit->setText(p);
        manualRb->setChecked(true);
    }
}

void SetupDialog::browse() {
    const QString d = QFileDialog::getExistingDirectory(this, u"심즈4 모드(Mods) 폴더 선택"_s, edit->text());
    if (!d.isEmpty()) {
        edit->setText(d);
        manualRb->setChecked(true);
    }
}

QString SetupDialog::chosen() const {
    QAbstractButton* b = group->checkedButton();
    if (!b)
        return {};
    if (b == manualRb)
        return edit->text().trimmed().isEmpty() ? QString() : coreCall("paths.normalize", {{"text"_L1, edit->text()}}).toString();
    return b->property("path").toString();
}

static std::pair<bool, QString> validateDir(const QString& d) {
    const QJsonArray r = coreCall("paths.validate", {{"dir"_L1, d}}).toArray();
    return {r[0].toBool(), r[1].toString()};
}

static QString modsOf(const QString& d) {
    // os.path.join(d, "Mods")
    return (d.endsWith(u'\\') || d.endsWith(u'/')) ? d + u"Mods"_s : d + u"\\Mods"_s;
}

void SetupDialog::validate() {
    const QString d = chosen();
    if (d.isEmpty()) {
        status->setText(QString());
        return;
    }
    const auto [ok, msg] = validateDir(d);
    status->setText(msg + u'\n' + modsOf(d));
    status->setObjectName(ok ? QString() : u"warn"_s);
    repolish(status);
}

void SetupDialog::acceptChoice() {
    const QString d = chosen();
    if (d.isEmpty())
        return;
    const auto [ok, msg] = validateDir(d);
    if (!ok) {
        if (!isDir(d)) {
            QMessageBox::warning(this, u"폴더 오류"_s, msg);
            return;
        }
        if (QMessageBox::question(this, u"확인"_s,
                                  msg + u"\n\n그래도 이 폴더를 사용할까요?"_s
                                      + (!isDir(modsOf(d)) ? u"\n(Mods 폴더를 새로 만듭니다)"_s : QString()))
            != QMessageBox::Yes)
            return;
        QDir().mkpath(modsOf(d));
    }
    resultDir = d;
    accept();
}

// ---------------------------------------------------------------- ImportDialog

ImportDialog::ImportDialog(const QJsonObject& plan, QWidget* parent) : QDialog(parent) {
    setWindowTitle(u"모드 추가"_s);
    resize(1150, 620);
    auto* lay = new QVBoxLayout(this);
    auto* t = new QLabel(u"추가할 모드를 확인하세요"_s);
    t->setObjectName(u"h1"_s);
    lay->addWidget(t);
    auto* sub = new QLabel(u"자동으로 종류를 판정했습니다. 잘못 인식됐다면 '종류'와 '분류'를 직접 바꿀 수 있습니다.\n"
                           u"· 스크립트 모드: 압축 안의 파일을 하나의 폴더로 묶어서 넣습니다.\n"
                           u"· CC (개별 분류): .package 파일을 각각 분류 폴더(헤어, 상의 등)로 넣습니다.\n"
                           u"· 트레이 (가족, 집, 방): Tray 폴더에 넣습니다."_s);
    sub->setObjectName(u"muted"_s);
    sub->setWordWrap(true);
    lay->addWidget(sub);

    tree = new QTreeWidget;
    tree->setColumnCount(4);
    tree->setHeaderLabels({u"파일"_s, u"종류 / 분류"_s, u"판정 근거"_s, u"비고"_s});
    QHeaderView* header = tree->header();
    header->setSectionResizeMode(QHeaderView::Interactive);
    header->setStretchLastSection(true);
    int col = 0;
    for (int width : {340, 230, 400})
        tree->setColumnWidth(col++, width);
    lay->addWidget(tree, 1);

    const QColor warnColor(tok("warn"));
    for (const auto& gv : plan["groups"_L1].toArray()) {
        const QJsonObject g = gv.toObject();
        auto* top = new QTreeWidgetItem(tree);
        top->setText(0, g["name"_L1].toString());
        QFont f = top->font(0);
        f.setWeight(QFont::DemiBold);
        top->setFont(0, f);
        top->setText(2, g["mode_reason"_L1].toString());
        top->setFirstColumnSpanned(false);
        auto* mode = new QComboBox;
        mode->addItem(u"스크립트 모드 (폴더째 추가)"_s, MODE_SCRIPT);
        mode->addItem(u"CC (개별 분류)"_s, MODE_CC);
        mode->setCurrentIndex(g["mode"_L1].toString() == MODE_SCRIPT ? 0 : 1);
        auto* nameEdit = new QLineEdit(g["folder"_L1].toString());
        nameEdit->setPlaceholderText(u"Mods 바로 아래"_s);
        nameEdit->setToolTip(u"넣을 폴더 이름 (Mods/이름)\n모르는 스크립트 모드는 Scripts 폴더에 모읍니다."_s);
        if (g["has_mods"_L1].toBool()) {
            tree->setItemWidget(top, 1, mode);
            tree->setItemWidget(top, 3, nameEdit);
        } else {
            top->setText(1, u"트레이"_s);
            // 화면에 붙지 않은 위젯은 대화상자가 맡아 지운다.
            mode->setParent(this);
            mode->hide();
            nameEdit->setParent(this);
            nameEdit->hide();
        }
        Row row;
        row.mode = mode;
        row.nameEdit = nameEdit;
        row.replaceChk = nullptr;
        for (const auto& tv : g["tray_items"_L1].toArray()) {
            const QJsonObject ti = tv.toObject();
            auto* tr = new QTreeWidgetItem(top);
            tr->setText(0, ti["name"_L1].toString());
            tr->setFlags(tr->flags() | Qt::ItemIsUserCheckable);
            tr->setCheckState(0, ti["include"_L1].toBool() ? Qt::Checked : Qt::Unchecked);
            tr->setText(1, u"트레이 · "_s + ti["kind_label"_L1].toString());
            tr->setText(2, u"Tray 폴더에 넣습니다 (파일 %1개)"_s.arg(ti["files"_L1].toInt()));
            if (ti["exists"_L1].toBool()) {
                tr->setText(3, u"이미 있음 (체크하면 덮어씁니다)"_s);
                tr->setForeground(3, warnColor);
            }
            row.trayRows << tr;
        }
        const QJsonArray entries = g["entries"_L1].toArray();
        row.entryCount = int(entries.size());
        int others = 0;
        for (int i = 0; i < entries.size(); ++i) {
            const QJsonObject e = entries[i].toObject();
            const QString kind = e["kind"_L1].toString();
            if (kind != u"package"_s && kind != u"script"_s) {
                if (kind != u"tray"_s)
                    ++others;
                continue;
            }
            auto* ch = new QTreeWidgetItem(top);
            ch->setText(0, e["rel"_L1].toString());
            ch->setFlags(ch->flags() | Qt::ItemIsUserCheckable);
            ch->setCheckState(0, e["include"_L1].toBool() ? Qt::Checked : Qt::Unchecked);
            CategoryCombo* combo = nullptr;
            const double confidence = e["confidence"_L1].toDouble();
            if (kind == u"package"_s) {
                combo = new CategoryCombo;
                combo->setKey(e["category"_L1].toString());
                tree->setItemWidget(ch, 1, combo);
                ch->setText(2, e["reason"_L1].toString() + (confidence != 0 ? u" (%1)"_s.arg(pyPercent(confidence)) : QString()));
            } else {
                ch->setText(1, u"스크립트 (.ts4script)"_s);
            }
            const QString duplicate = e["duplicate"_L1].toString();
            if (!duplicate.isEmpty()) {
                ch->setText(3, u"이미 있음: "_s + duplicate);
                ch->setForeground(3, warnColor);
            } else if (kind == u"package"_s && confidence < 0.6) {
                ch->setText(3, u"판정 불확실 — 확인 필요"_s);
                ch->setForeground(3, warnColor);
            }
            row.children.append({ch, i, combo});
        }
        if (others) {
            auto* o = new QTreeWidgetItem(top);
            o->setText(0, u"기타 파일 %1개 (이미지/설정 등)"_s.arg(others));
            o->setText(2, u"스크립트 모드일 때만 함께 복사됩니다 (이미 있는 설정 파일은 덮어쓰지 않음)"_s);
            o->setForeground(0, QColor(tok("muted")));
        }
        if (!g["replaces"_L1].isNull()) {
            auto* r = new QTreeWidgetItem(top);
            row.replaceChk = new QCheckBox(u"기존 버전 교체"_s);
            row.replaceChk->setChecked(g["replace"_L1].toBool());
            tree->setItemWidget(r, 0, row.replaceChk);
            r->setText(1, u"설치됨: "_s + g["replaces"_L1].toString());
            QString note = u"옛 .ts4script/.package는 휴지통으로 보내고, 설정 파일(.cfg 등)은 그대로 둡니다."_s;
            const QString keep = g["keep_note"_L1].toString();
            if (!keep.isEmpty())
                note += u' ' + keep;
            r->setText(2, note);
            r->setToolTip(2, note);
        }
        for (const auto& lv : g["missing_libs"_L1].toArray()) {
            const QJsonObject lib = lv.toObject();
            auto* w = new QTreeWidgetItem(top);
            w->setText(0, u"필요한 라이브러리 없음: "_s + lib["name"_L1].toString());
            const QString url = lib["url"_L1].toString();
            w->setText(2, u"설치되어 있지 않거나 비활성화되어 있습니다. 이 모드가 작동하려면 함께 설치해야 합니다."_s
                              + (url.isEmpty() ? QString() : u' ' + url));
            w->setToolTip(2, w->text(2));
            for (int c : {0, 2})
                w->setForeground(c, warnColor);
        }
        top->setExpanded(true);
        const QList<Child> children = row.children;
        connect(mode, &QComboBox::currentIndexChanged, this, [mode, children, nameEdit] { modeChanged(mode, children, nameEdit); });
        modeChanged(mode, children, nameEdit);
        rows << row;
    }

    QStringList errors;
    for (const auto& e : plan["errors"_L1].toArray())
        errors << e.toString();
    if (!errors.isEmpty()) {
        auto* err = new QLabel(errors.join(u'\n'));
        err->setObjectName(u"warn"_s);
        err->setWordWrap(true);
        lay->addWidget(err);
    }

    auto* bb = new QDialogButtonBox;
    QPushButton* ok = bb->addButton(u"추가"_s, QDialogButtonBox::AcceptRole);
    ok->setObjectName(u"primary"_s);
    bb->addButton(u"취소"_s, QDialogButtonBox::RejectRole);
    connect(bb, &QDialogButtonBox::accepted, this, [this] { acceptPlan(); });
    connect(bb, &QDialogButtonBox::rejected, this, &QDialog::reject);
    lay->addWidget(bb);
}

void ImportDialog::modeChanged(QComboBox* mode, const QList<Child>& children, QLineEdit* nameEdit) {
    const bool script = mode->currentData().toString() == MODE_SCRIPT;
    nameEdit->setEnabled(script);
    for (const Child& c : children)
        if (c.combo)
            c.combo->setEnabled(!script);
}

void ImportDialog::acceptPlan() {
    edits_ = QJsonArray();
    for (const Row& row : rows) {
        QJsonObject g;
        QJsonArray trays;
        for (QTreeWidgetItem* tr : row.trayRows)
            trays << (tr->checkState(0) == Qt::Checked);
        g["trays"_L1] = trays;
        g["mode"_L1] = row.mode->currentData().toString();
        g["folder"_L1] = row.nameEdit->text().trimmed();
        if (row.replaceChk)
            g["replace"_L1] = row.replaceChk->isChecked();
        QJsonArray entries;
        for (int i = 0; i < row.entryCount; ++i)
            entries << QJsonValue();
        for (const Child& c : row.children) {
            QJsonObject e{{"include"_L1, c.item->checkState(0) == Qt::Checked}};
            if (c.combo)
                e["category"_L1] = c.combo->key();
            entries[c.entryIndex] = e;
        }
        g["entries"_L1] = entries;
        edits_ << g;
    }
    accept();
}
