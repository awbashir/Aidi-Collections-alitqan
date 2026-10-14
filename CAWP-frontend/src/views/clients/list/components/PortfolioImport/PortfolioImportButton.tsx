/**
 * The /clients "add clients" button: opens the portfolio upload wizard (mapping, preview,
 * one-transaction import). Shown when the role has `clints_add` (checked by the list).
 * The clients list reloads when an import succeeds.
 */
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiPlusCircle } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import { useFetchClients } from '../../hooks/GetClientHook'
import PortfolioImportDialog from './PortfolioImportDialog'

const PortfolioImportButton = () => {
    const { t } = useTranslation()
    const [open, setOpen] = useState(false)
    const fetchClients = useFetchClients()
    const onImported = useCallback(() => fetchClients(), [fetchClients])

    return (
        <>
            <Button variant="solid" size="sm" icon={<HiPlusCircle />} onClick={() => setOpen(true)} data-testid="p7-import-open">
                {t('p7.import.button')}
            </Button>
            {open && <PortfolioImportDialog isOpen={open} onClose={() => setOpen(false)} onImported={onImported} />}
        </>
    )
}

export default PortfolioImportButton
