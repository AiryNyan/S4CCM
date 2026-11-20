#include "symlink_page.h"

static bool gameRunning() {
    return coreCall("launcher.running").toBool();
}

static QString gb(double bytes) {
    return QString::number(bytes / 1073741824.0, 'f', 1) + u" GB"_s;
}

SymlinkPage::SymlinkPage() {
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(20, 16, 20, 16);
    lay->setSpacing(10);
    lay->addWidget(pageTitle(
        u"심볼릭 링크"_s,
        u"Mods 폴더를 다른 위치(예: 공간이 넉넉한 D 드라이브)로 옮기고, 원래 자리에는 심볼릭 링크를 남깁니다.\n"
        u"게임과 S4CCM은 지금처럼 내 문서의 Mods 경로로 읽지만, 실제 파일은 옮긴 곳에 저장됩니다.\n"
        u"\n"
        u"1. [옮길 경로 선택…]으로 Mods를 둘 폴더를 고르세요. 고른 폴더 안에 Mods 폴더가 만들어집니다.\n"
        u"2. [Mods 폴더 옮기기]를 누르고 안내를 확인하면 옮깁니다.\n"
        u"\n"
        u"• 비활성(S4CCM_Disabled)과 충돌 테스트(S4CCM_Quarantine) 폴더도 함께 옮깁니다.\n"
        u"• 심볼릭 링크를 만들려면 관리자 권한이 필요합니다. 필요하면 S4CCM을 관리자 권한으로 껐다 켭니다.\n"
        u"• 옮기는 동안 게임을 끄고, Mods 폴더를 열어 둔 탐색기 창도 닫아 주세요."_s));

    auto* stateBox = new QGroupBox(u"현재 상태"_s);
    auto* sl = new QVBoxLayout(stateBox);
    statusText = new QLabel;
    statusText->setTextFormat(Qt::RichText);
    statusText->setWordWrap(true);
    statusText->setTextInteractionFlags(Qt::TextSelectableByMouse);
    sl->addWidget(statusText);
    lay->addWidget(stateBox);

    auto* moveBox = new QGroupBox(u"Mods 폴더 옮기기"_s);
    auto* ml = new QVBoxLayout(moveBox);
    auto* row = new QHBoxLayout;
    destEdit = new QLineEdit;
    destEdit->setReadOnly(true);
    destEdit->setPlaceholderText(u"옮길 경로를 선택하세요"_s);
    row->addWidget(destEdit, 1);
    bPick = new QPushButton(u"옮길 경로 선택…"_s);
    connect(bPick, &QPushButton::clicked, this, [this] { pickDest(); });
    row->addWidget(bPick);
    ml->addLayout(row);
    destInfo = new QLabel;
    destInfo->setObjectName(u"muted"_s);
    destInfo->setWordWrap(true);
    ml->addWidget(destInfo);
    bMove = new QPushButton(u"Mods 폴더 옮기기"_s);
    bMove->setObjectName(u"primary"_s);
    connect(bMove, &QPushButton::clicked, this, [this] { startMove(); });
    ml->addWidget(bMove, 0, Qt::AlignLeft);
    lay->addWidget(moveBox);

    auto* backBox = new QGroupBox(u"원래 위치로 되돌리기"_s);
    auto* bl = new QVBoxLayout(backBox);
    auto* backInfo = new QLabel(u"심볼릭 링크를 지우고, 옮겨 두었던 파일을 원래 위치(내 문서의 심즈 4 폴더)로 다시 가져옵니다. "
                                u"관리자 권한은 필요하지 않습니다."_s);
    backInfo->setObjectName(u"muted"_s);
    backInfo->setWordWrap(true);
    bl->addWidget(backInfo);
    bRestore = new QPushButton(u"원래 위치로 되돌리기"_s);
    connect(bRestore, &QPushButton::clicked, this, [this] { restore(); });
    bl->addWidget(bRestore, 0, Qt::AlignLeft);
    lay->addWidget(backBox);
    lay->addStretch(1);
    // 검증용: 폴더 선택 창 없이 옮길 경로를 채워 둔다.
    if (qEnvironmentVariableIsSet("S4CCM_TEST_DEST"))
        destEdit->setText(QDir::toNativeSeparators(qEnvironmentVariable("S4CCM_TEST_DEST")));
}

QString SymlinkPage::destMods(const QString& dest) const {
    const QString d = QDir::toNativeSeparators(dest);
    return d.endsWith(u'\\') ? d + u"Mods"_s : d + u"\\Mods"_s;
}

void SymlinkPage::refreshPage() {
    linked_ = false;
    QStringList lines;
    const QString muted = tok("muted");
    for (const auto& v : coreCall("relocate.status").toArray()) {
        const QJsonObject o = v.toObject();
        const QString name = o["name"_L1].toString();
        if (name != u"Mods"_s)   // 상태 칸에는 Mods 위치만 보여 준다 (비활성·충돌 테스트 폴더도 함께 옮겨지지만 표시하지 않음)
            continue;
        QString state;
        if (o["link"_L1].toBool()) {
            state = u"심볼릭 링크 → <b>%1</b>"_s.arg(o["target"_L1].toString().toHtmlEscaped());
            if (name == u"Mods"_s)
                linked_ = true;
        } else if (o["exists"_L1].toBool()) {
            state = u"일반 폴더 <span style=\"color:%1\">(%2)</span>"_s.arg(muted, o["path"_L1].toString().toHtmlEscaped());
        } else {
            state = u"<span style=\"color:%1\">아직 없음</span>"_s.arg(muted);
        }
        lines << u"<b>%1</b>&nbsp;&nbsp;%2"_s.arg(name.toHtmlEscaped(), state);
    }
    statusText->setText(lines.join(u"<br>"_s));
    destInfo->setText(destEdit->text().isEmpty() ? QString() : u"실제 Mods 위치: "_s + destMods(destEdit->text()));
    bMove->setEnabled(!linked_ && !cfg().simsDir.isEmpty());
    bPick->setEnabled(!linked_);
    bRestore->setEnabled(linked_);
}

void SymlinkPage::pickDest() {
    const QString d = QFileDialog::getExistingDirectory(this, u"Mods를 옮길 폴더 선택"_s, destEdit->text());
    if (d.isEmpty())
        return;
    destEdit->setText(QDir::toNativeSeparators(d));
    refreshPage();
}

void SymlinkPage::startMove() {
    const QString dest = destEdit->text();
    if (dest.isEmpty()) {
        QMessageBox::information(this, u"Mods 폴더 옮기기"_s, u"먼저 [옮길 경로 선택…]으로 옮길 폴더를 고르세요."_s);
        return;
    }
    if (gameRunning()) {
        QMessageBox::warning(this, u"Mods 폴더 옮기기"_s, u"게임이 실행 중입니다. 게임을 끈 뒤 옮기세요."_s);
        return;
    }
    QJsonObject m;
    try {
        WaitCursor wait;
        m = coreCall("relocate.measure", {{"dest"_L1, dest}}).toObject();
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"Mods 폴더 옮기기"_s, ex.msg);
        return;
    }
    const QString mods = libAbs(u"Mods"_s);
    QMessageBox box(this);
    box.setWindowTitle(u"Mods 폴더 옮기기"_s);
    box.setIcon(QMessageBox::Warning);
    box.setTextFormat(Qt::RichText);
    box.setText(u"<b>%1</b><br>에 있는 Mods는 가상의 폴더로 바로가기와 같습니다.<br><br>"
                u"<b>%2</b><br>가 실제 Mods 경로로 추후 이 폴더를 지우시면<br>실제로도 지워집니다"_s
                    .arg(mods.toHtmlEscaped(), destMods(dest).toHtmlEscaped()));
    // 확인 창이 서식 있는 글(RichText)이라 줄바꿈은 <br> 로 쓴다.
    QString info = u"옮길 파일: %1개 · %2<br>비활성/충돌 테스트 폴더도 함께 옮깁니다."_s
                       .arg(QLocale(QLocale::Korean).toString(m["files"_L1].toInteger()), gb(m["bytes"_L1].toDouble()));
    if (!m["same_volume"_L1].toBool())
        info += u"<br>다른 드라이브라서 복사한 뒤 원래 자리의 파일을 지웁니다. 시간이 걸릴 수 있습니다."_s;
    box.setInformativeText(info);
    QPushButton* yes = box.addButton(u"확인했습니다"_s, QMessageBox::AcceptRole);
    QPushButton* no = box.addButton(u"취소"_s, QMessageBox::RejectRole);
    box.setDefaultButton(no);
    box.setEscapeButton(no);
    box.exec();
    if (box.clickedButton() != yes)
        return;

    // 개발자 모드나 관리자 권한이면 바로 만들 수 있다. S4CCM_TEST_ELEVATE 는 검증용.
    if (!coreCall("relocate.can_symlink").toBool() || qEnvironmentVariableIsSet("S4CCM_TEST_ELEVATE")) {
        QMessageBox ask(this);
        ask.setWindowTitle(u"관리자 권한 필요"_s);
        ask.setIcon(QMessageBox::Information);
        ask.setText(u"심볼릭 링크를 만들려면 관리자 권한이 필요합니다.\nS4CCM을 관리자 권한으로 껐다 켭니다."_s);
        ask.setInformativeText(u"다시 켜지면 Mods 폴더 옮기기를 이어서 진행합니다."_s);
        QPushButton* go = ask.addButton(u"관리자 권한으로 다시 실행"_s, QMessageBox::AcceptRole);
        QPushButton* cancel = ask.addButton(u"취소"_s, QMessageBox::RejectRole);
        ask.setEscapeButton(cancel);
        ask.exec();
        if (ask.clickedButton() == go && requestElevation)
            requestElevation(dest);
        return;
    }
    if (runRelocate(dest))
        QMessageBox::information(this, u"Mods 폴더 옮기기"_s, u"Mods 폴더를 옮겼습니다.\n실제 위치: "_s + destMods(dest));
}

bool SymlinkPage::runRelocate(const QString& dest, bool elevated) {
    destEdit->setText(QDir::toNativeSeparators(dest));
    bool ok = true;
    try {
        runWithLoading(this, u"Mods 폴더를 옮기는 중"_s, [dest](const LoadProgress& progress) {
            coreCall("relocate.run", {{"dest"_L1, dest}}, stepped(progress));
            coreCall("lib.scan", {}, phased(progress, u"목록에 반영하는 중"_s));
            return QJsonValue();
        });
    } catch (const CoreError& ex) {
        ok = false;
        QMessageBox::warning(this, u"Mods 폴더 옮기기"_s,
                             u"Mods 폴더를 옮기지 못했습니다.\n\n"_s + ex.msg
                                 + (elevated ? u"\n\n일반 권한으로 다시 켭니다."_s : QString()));
    }
    if (changed)
        changed();
    refreshPage();
    if (ok && elevated)
        QMessageBox::information(this, u"Mods 폴더 옮기기"_s,
                                 u"Mods 폴더를 옮겼습니다.\n실제 위치: %1\n\n일반 권한으로 다시 켭니다."_s.arg(destMods(dest)));
    return ok;
}

void SymlinkPage::restore() {
    QString target;
    for (const auto& v : coreCall("relocate.status").toArray())
        if (v.toObject()["name"_L1].toString() == u"Mods"_s)
            target = v.toObject()["target"_L1].toString();
    const QString mods = libAbs(u"Mods"_s);
    if (gameRunning()) {
        QMessageBox::warning(this, u"원래 위치로 되돌리기"_s, u"게임이 실행 중입니다. 게임을 끈 뒤 되돌리세요."_s);
        return;
    }
    if (!confirm(this, u"원래 위치로 되돌리기"_s,
                 u"%1 에 있는 파일을 원래 위치(%2)로 옮기고 심볼릭 링크를 지웁니다.\n"
                 u"비활성/충돌 테스트 폴더도 함께 되돌립니다.\n\n계속할까요?"_s.arg(target, mods)))
        return;
    try {
        runWithLoading(this, u"원래 위치로 되돌리는 중"_s, [](const LoadProgress& progress) {
            coreCall("relocate.restore", {}, stepped(progress));
            coreCall("lib.scan", {}, phased(progress, u"목록에 반영하는 중"_s));
            return QJsonValue();
        });
        QMessageBox::information(this, u"원래 위치로 되돌리기"_s, u"Mods 폴더를 원래 위치로 되돌렸습니다.\n"_s + mods);
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"원래 위치로 되돌리기"_s, u"되돌리지 못했습니다.\n\n"_s + ex.msg);
    }
    if (changed)
        changed();
    refreshPage();
}
