import { Link } from 'react-router-dom'
import { AirFrame } from './AirFrame'

/** Неизвестный адрес: понятное сообщение вместо пустого экрана. */
export function NotFoundPage() {
  return (
    <AirFrame label="Страница не найдена" tabs>
      <section className="bf-screen" aria-label="Страница не найдена">
        <div className="bf-inner bf-page bf-notfound">
          <div className="bf-notfound-code" aria-hidden="true">404</div>
          <h1 className="bf-h2">Такой страницы нет</h1>
          <p className="bf-intro">Возможно, ссылка устарела или в адресе опечатка.</p>
          <div className="bf-notfound-actions">
            <Link to="/" className="bf-primary">На главную</Link>
            <Link to="/my" className="bf-notfound-link">Мои записи</Link>
          </div>
        </div>
      </section>
    </AirFrame>
  )
}
