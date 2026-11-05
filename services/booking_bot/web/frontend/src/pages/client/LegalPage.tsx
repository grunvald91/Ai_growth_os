import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ShieldCheck, FileText, ScrollText, AlertTriangle } from 'lucide-react'
import { api, type BusinessDetail } from '../../api/client'
import { ClientShell } from '../../components/ClientShell'

const LEGAL_VERSION = '2026-10-06-v1'
// Политика обновлена 2026-10-24 (вход через Яндекс ID). Версия согласия и условий записи не менялась,
// поэтому отметки о принятых при записи документах (LEGAL_DOCUMENT_VERSION на сервере) остаются верными.
const PRIVACY_VERSION = '2026-10-24-v2'

type DocType = 'privacy' | 'consent' | 'terms'

function OperatorCard({ business }: { business: BusinessDetail }) {
  return (
    <div className="ds-card" style={{ padding: 14, marginBottom: 14 }}>
      <h2 className="ds-h3" style={{ marginBottom: 8 }}>Оператор / исполнитель</h2>
      <div className="ds-sub" style={{ lineHeight: 1.7 }}>
        <div><b>{business.legal_name || business.name}</b></div>
        {business.legal_inn && <div>ИНН: {business.legal_inn}</div>}
        {business.legal_ogrn && <div>ОГРН / ОГРНИП: {business.legal_ogrn}</div>}
        {business.legal_address && <div>Адрес: {business.legal_address}</div>}
        {business.legal_email && <div>Email по вопросам персональных данных: {business.legal_email}</div>}
        {business.phone && <div>Телефон: {business.phone}</div>}
      </div>
    </div>
  )
}

function Privacy({ business }: { business: BusinessDetail }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <ShieldCheck size={22} />
        <h1 className="ds-h1">Политика обработки персональных данных</h1>
      </div>
      <p className="ds-sub" style={{ marginBottom: 14 }}>Версия: {PRIVACY_VERSION}</p>
      <OperatorCard business={business} />

      <section className="ds-card" style={{ padding: 14, display: 'grid', gap: 14 }}>
        <div>
          <h2 className="ds-h3">1. Какие данные обрабатываются</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Имя, номер телефона, данные Telegram при использовании Telegram-авторизации,
            выбранные услуга/специалист/дата/время, комментарий к записи, сведения о статусе
            бронирования и предоплаты, загруженный клиентом файл подтверждения оплаты,
            а также технические данные, необходимые для работы сайта и PWA. При входе через
            Яндекс ID дополнительно: идентификатор и логин в Яндексе, имя и фамилия, адрес
            электронной почты, номер телефона и дата рождения — только те данные, передачу
            которых пользователь разрешил на экране согласия Яндекса.
          </p>
        </div>
        <div>
          <h2 className="ds-h3">2. Цели обработки</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Создание и исполнение записи на услугу, связь с клиентом, подтверждение и перенос
            записи, обработка предоплаты, отправка сервисных напоминаний, обеспечение
            безопасности и работоспособности сервиса, рассмотрение обращений клиента.
            Для входа через Яндекс ID: опознание пользователя; объединение аккаунтов одного
            человека только при совпадении данных — номера телефона, подтверждённого Яндексом, с
            номером аккаунта Telegram (после ввода кода, отправленного в этот Telegram, или при
            входе через Telegram в том же браузере) либо почты, подтверждённой Яндексом, с почтой
            для входа по паролю, подтверждённой кодом; а также поздравление пользователя с днём
            рождения, если дата передана. От поздравлений можно отказаться, написав оператору.
          </p>
        </div>
        <div>
          <h2 className="ds-h3">3. Правовые основания</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Федеральный закон № 152-ФЗ «О персональных данных», действия по инициативе клиента
            для заключения и исполнения договора на услугу, а также отдельное согласие клиента
            в случаях, когда оно является выбранным оператором правовым основанием обработки.
          </p>
        </div>
        <div>
          <h2 className="ds-h3">4. Действия с данными и срок</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Сбор, запись, систематизация, накопление, хранение, уточнение, использование,
            предоставление уполномоченным получателям, блокирование и удаление. Данные
            хранятся не дольше, чем этого требуют цели обработки и применимые обязательные
            сроки хранения, после чего удаляются или обезличиваются, если иное не предусмотрено законом.
          </p>
        </div>
        <div>
          <h2 className="ds-h3">5. ServGo и каналы связи</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            ServGo используется как техническая платформа для записи и обработки данных по
            поручению оператора. При использовании Telegram часть данных, необходимая для
            уведомлений и подтверждения записи, может передаваться через инфраструктуру
            Telegram. Трансграничная передача, если она возникает, осуществляется оператором
            только при выполнении требований законодательства РФ.
          </p>
        </div>
        <div>
          <h2 className="ds-h3">6. Вход через Яндекс ID</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Вход через Яндекс ID необязателен. Данные передаёт компания «Яндекс» по разрешению
            пользователя на странице согласия Яндекса: ServGo не получает пароль от аккаунта
            Яндекса и не имеет доступа к другим сервисам Яндекса. Мы сохраняем идентификатор
            и логин Яндекса для повторного входа, подтверждённые Яндексом почту и телефон — для
            объединения аккаунтов по совпадению, а имя, почту, номер телефона и дату рождения
            используем в целях, указанных в разделе 2. Пользователь может в любой момент
            отозвать доступ в настройках Яндекс ID, а также попросить оператора отвязать Яндекс
            или удалить сохранённые из Яндекса данные (адрес оператора указан выше).
          </p>
        </div>
        <div>
          <h2 className="ds-h3">7. Техническое хранение на устройстве</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            ServGo использует техническое локальное хранилище браузера для авторизации,
            сохранения ссылки на запись, настроек интерфейса и работы PWA. Рекламные cookies
            и сторонние рекламные трекеры в текущем клиентском приложении не требуются для
            оформления записи.
          </p>
        </div>
        <div>
          <h2 className="ds-h3">8. Права клиента</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Клиент вправе запросить сведения об обработке его данных, потребовать их уточнения,
            блокирования или удаления при наличии предусмотренных законом оснований, а также
            отозвать ранее данное согласие. Обращения направляются на email оператора, указанный выше.
          </p>
        </div>
      </section>
    </>
  )
}

function Consent({ business }: { business: BusinessDetail }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <FileText size={22} />
        <h1 className="ds-h1">Согласие на обработку персональных данных</h1>
      </div>
      <p className="ds-sub" style={{ marginBottom: 14 }}>Версия: {LEGAL_VERSION}</p>
      <OperatorCard business={business} />
      <section className="ds-card" style={{ padding: 14 }}>
        <p className="ds-sub" style={{ lineHeight: 1.7, marginBottom: 12 }}>
          Нажимая отдельную галочку «Даю согласие на обработку персональных данных» перед
          оформлением записи, я свободно, своей волей и в своем интересе даю оператору,
          указанному выше, конкретное, информированное и однозначное согласие на обработку
          моих персональных данных для оформления и сопровождения записи.
        </p>
        <p className="ds-sub" style={{ lineHeight: 1.7, marginBottom: 12 }}>
          Состав данных: имя, номер телефона, данные Telegram при использовании Telegram,
          сведения о выбранной услуге, специалисте, дате и времени, комментарий к записи,
          сведения о статусе бронирования и предоплаты и файл подтверждения оплаты, если я
          его загружаю.
        </p>
        <p className="ds-sub" style={{ lineHeight: 1.7, marginBottom: 12 }}>
          Разрешенные действия: сбор, запись, систематизация, накопление, хранение, уточнение,
          использование, предоставление лицам, привлекаемым оператором для технического
          обеспечения записи и коммуникации, блокирование и удаление.
        </p>
        <p className="ds-sub" style={{ lineHeight: 1.7 }}>
          Согласие действует до достижения целей обработки либо до его отзыва, если дальнейшая
          обработка не требуется или не допускается на ином законном основании. Отзыв можно
          направить на {business.legal_email || 'email оператора, указанный в политике'}.
        </p>
      </section>
    </>
  )
}

function Terms({ business }: { business: BusinessDetail }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <ScrollText size={22} />
        <h1 className="ds-h1">Условия записи и оказания услуги</h1>
      </div>
      <p className="ds-sub" style={{ marginBottom: 14 }}>Версия: {LEGAL_VERSION}</p>
      <OperatorCard business={business} />
      <section className="ds-card" style={{ padding: 14, display: 'grid', gap: 14 }}>
        <div>
          <h2 className="ds-h3">Оформление записи</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Клиент выбирает услугу, специалиста или ресурс, дату и время, проверяет стоимость
            и передает контактные данные. Нажатие «Записаться» после принятия настоящих условий
            направляет оператору заявку на выбранную услугу.
          </p>
        </div>
        {business.prepayment_enabled && (
          <div>
            <h2 className="ds-h3">Предоплата</h2>
            <p className="ds-sub" style={{ lineHeight: 1.65 }}>
              Для выбранных услуг оператор может требовать предоплату. Размер и реквизиты
              показываются до завершения оформления. После перевода клиент может прикрепить
              подтверждение оплаты непосредственно в ServGo; владелец получает уведомление.
            </p>
          </div>
        )}
        <div>
          <h2 className="ds-h3">Отмена и перенос</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65, whiteSpace: 'pre-line' }}>
            {business.cancellation_policy_text?.trim()
              || 'Конкретные условия отмены и переноса сообщает исполнитель. Императивные права потребителя, установленные законодательством РФ, сохраняются независимо от текста этих условий.'}
          </p>
        </div>
        <div>
          <h2 className="ds-h3">Кассовый чек</h2>
          <p className="ds-sub" style={{ lineHeight: 1.65 }}>
            Загружаемый клиентом банковский скриншот или файл подтверждает перевод, но сам по
            себе не заменяет кассовый чек. Обязанность сформировать и передать кассовый чек
            определяется применимыми к исполнителю правилами законодательства о ККТ.
          </p>
        </div>
      </section>
    </>
  )
}

export function LegalPage() {
  const { docType, businessId } = useParams<{ docType: DocType; businessId: string }>()
  const [business, setBusiness] = useState<BusinessDetail | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    const id = Number(businessId)
    if (!id) {
      setError(true)
      return
    }
    api.getBusiness(id).then(setBusiness).catch(() => setError(true))
  }, [businessId])

  const title = useMemo(() => {
    if (docType === 'privacy') return 'Политика конфиденциальности'
    if (docType === 'consent') return 'Согласие на ПД'
    return 'Условия записи'
  }, [docType])

  if (error) {
    return (
      <ClientShell title="Документы" backTo={businessId ? `/book/${businessId}` : '/'}>
        <div className="ds-card" style={{ padding: 16, marginTop: 12 }}>
          <AlertTriangle size={24} />
          <h2 className="ds-h2" style={{ marginTop: 8 }}>Документ недоступен</h2>
          <p className="ds-sub">Не удалось загрузить юридические данные бизнеса.</p>
        </div>
      </ClientShell>
    )
  }

  if (!business) {
    return <ClientShell title="Документы" backTo={businessId ? `/book/${businessId}` : '/'}><div className="skeleton" style={{ height: 220, marginTop: 12 }} /></ClientShell>
  }

  const enabled = !!business.legal_documents_enabled
  return (
    <ClientShell title={title} backTo={`/book/${business.id}`}>
      <div style={{ paddingTop: 12, paddingBottom: 24 }}>
        {!enabled && (
          <div className="bk-info-box is-amber" style={{ marginBottom: 14 }}>
            <h3><AlertTriangle size={16} /> Документы ещё не включены владельцем</h3>
            <p>Юридические реквизиты необходимо заполнить и подтвердить в настройках бизнеса.</p>
          </div>
        )}
        {docType === 'privacy' ? <Privacy business={business} />
          : docType === 'consent' ? <Consent business={business} />
          : <Terms business={business} />}

        <div className="ds-card" style={{ padding: 14, marginTop: 14 }}>
          <div className="ds-sub" style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            <Link className="ds-accent" to={`/legal/privacy/${business.id}`}>Политика ПД</Link>
            <Link className="ds-accent" to={`/legal/consent/${business.id}`}>Согласие на ПД</Link>
            <Link className="ds-accent" to={`/legal/terms/${business.id}`}>Условия записи</Link>
          </div>
        </div>
      </div>
    </ClientShell>
  )
}
