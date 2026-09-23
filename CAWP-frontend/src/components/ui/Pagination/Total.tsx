import { useTranslation } from 'react-i18next'

const Total = (props: { total: number }) => {
    const { total } = props
    const { t } = useTranslation()
    return (
        <div className="pagination-total">
            {t('pagination.total')}{' '}
            <span className="tabular-nums">
                {new Intl.NumberFormat('en-US').format(total || 0)}
            </span>{' '}
            {t('pagination.items')}
        </div>
    )
}

export default Total
