

import React from 'react'
import { useTranslation } from 'react-i18next'
import Card from '@/components/ui/Card'
import Avatar from '@/components/ui/Avatar'
import { HiOutlineCurrencyDollar, HiOutlineCash } from 'react-icons/hi'
import { NumericFormat } from 'react-number-format'
import { SaudiRiyal } from 'lucide-react';
import Select from '@/components/ui/Select'
import { useState } from 'react'
import Table from '@/components/ui/Table'







const CommentReportDaily = ({ data = [], className }: CommentReportDailyProps) => {
    const { t } = useTranslation()

    const [contracts, setContracts] = useState<Contract[]>([]);
    const [selectedContracts_, setSelectedContracts_] = useState([]);
    const [users, setUsers] = useState<User[]>([]);
    const [comments, setComments] = useState<Comment[]>([]);

    // Move the setState call inside useEffect to prevent infinite re-renders
    React.useEffect(() => {
        setContracts(Array.isArray(data?.contracts) ? data.contracts.filter(Boolean) : []);
        setUsers(Array.isArray(data?.users) ? data.users.filter(Boolean) : []);
        setComments(Array.isArray(data?.comments) ? data.comments.filter(Boolean) : []);
    }, [data]);

    const { Tr, Th, Td, THead, TBody } = Table



  return (
    <Card className={className}>
        <div>
            <div className="mb-6">
                <h4 className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong mb-4">
                    {t('reportCommtsByHour')}
                </h4>
                 <div className="w-64">
                    <Select
                        options={contracts.map(contract => ({
                            label: contract.name,
                            value: contract.id
                        }))}
                        value={selectedContracts_}
                        onChange={(value) => setSelectedContracts_(value)}
                        placeholder={t('select_contract')}
                        isClearable
                        className="mb-4"
                    />
                </div> 
            </div>

            {/* Table list of users */}
            <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
<Table compact >
                    <THead>
                        <Tr>
                            <Th className="px-1 py-3 text-start w-16">
                                #
                            </Th>
                            <Th className="px-1 py-3 text-start ">
                                {t('users.name')}
                            </Th>
                            {(() => {
                                const hours = [];
                                const now = new Date();
                                //for testing purpose, use yesterday as "now"
                                //now.setDate(now.getDate() - 1);
                                const currentHour = now.getHours();
                                const endHour = Math.min(currentHour, 17); // Stop at current time or 17:00
                                
                                for (let hour = 8; hour <= endHour; hour++) {
                                    const timeString = `${hour.toString().padStart(2, '0')}:00`;
                                    hours.push(
                                        <Th key={hour} className="px-1 py-3 text-center w-20">
                                            {timeString}
                                        </Th>
                                    );
                                }
                                return hours;
                            })()}
                            <Th className="px-1 py-3 text-center w-20 bg-gray-100 dark:bg-night-raised">
                                {t('total')}
                            </Th>
                        </Tr>
                    </THead>
                    <TBody>
                        {users
                            .filter(user => {
                                // If no contracts are selected, show all users
                                if (!selectedContracts_ || selectedContracts_.length === 0) {
                                    return true;
                                }
                                // Ensure selectedContracts_ is an array
                                const selectedContractsArray = Array.isArray(selectedContracts_) 
                                    ? selectedContracts_ 
                                    : [selectedContracts_];
                                
                                // Filter users based on selected contracts
                                return selectedContractsArray.some(contractId => 
                                    user.contract_id === contractId.value
                                );
                            })
                            .sort((userA, userB) => {
                                // Calculate total comments for each user
                                const now = new Date();
                                //for testing purpose, use yesterday as "now"
                                //now.setDate(now.getDate() - 1);
                                const currentHour = now.getHours();
                                const endHour = Math.min(currentHour, 17);
                                const today = new Date();
                                //for testing purpose, use yesterday as "now"
                                //today.setDate(today.getDate() - 1);

                                const totalA = comments.filter(comment => {
                                    if (comment.user_id !== userA.id) return false;
                                    const commentDate = new Date(comment.create_at);
                                    const commentHour = commentDate.getHours();
                                    const isToday = commentDate.toDateString() === today.toDateString();
                                    return isToday && commentHour >= 8 && commentHour <= endHour;
                                }).length;
                                
                                const totalB = comments.filter(comment => {
                                    if (comment.user_id !== userB.id) return false;
                                    const commentDate = new Date(comment.create_at);
                                    const commentHour = commentDate.getHours();
                                    const isToday = commentDate.toDateString() === today.toDateString();
                                    return isToday && commentHour >= 8 && commentHour <= endHour;
                                }).length;
                                
                                // Sort descending (highest total comments first)
                                return totalB - totalA;
                            })
                            .map((user, index) => (
                            <Tr key={user.id} className="hover:bg-gray-50 dark:hover:bg-night-raised/60">
                                <Td className="px-1 py-4 whitespace-nowrap text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                    {index + 1}
                                </Td>
                                <Td className="px-1 py-4 whitespace-nowrap text-[13.5px] text-gray-700 dark:text-night-body">
                                    {[user?.frist_name, user?.last_name].filter(Boolean).join(' ') || '—'}
                                </Td>
                             {(() => {
                                    const cells = [];
                                    const now = new Date();
                                    //for testing purpose, use yesterday as "now"
                                    //now.setDate(now.getDate() - 1);
                                    const currentHour = now.getHours();
                                    const endHour = Math.min(currentHour, 17);
                                    let totalActiveComments = 0;
                                    let totalInactiveComments = 0;
                                    
                                    for (let hour = 8; hour <= endHour; hour++) {
                                        // Filter comments for this user at this specific hour
                                        const userHourComments = comments.filter(comment => {
                                            if (comment.user_id !== user.id) return false;
                                            
                                            // Parse comment timestamp and check if it's within this hour
                                            const commentDate = new Date(comment.create_at);
                                            const commentHour = commentDate.getHours();
                                            
                                            // Check if comment is from today and within this hour
                                            const today = new Date();
                                            //for testing purpose, use yesterday as "now"
                                            //today.setDate(today.getDate() - 1);
                                            const isToday = commentDate.toDateString() === today.toDateString();
                                            
                                            return isToday && commentHour === hour;
                                        });
                                        
                                        // Separate active and inactive comments
                                        const activeComments = userHourComments.filter(comment => comment?.status?.is_active);
                                        const inactiveComments = userHourComments.filter(comment => !comment?.status?.is_active);

                                        totalActiveComments += activeComments.length;
                                        totalInactiveComments += inactiveComments.length;
                                        
                                        cells.push(
                                            <Td key={hour} className="px-1 py-4 whitespace-nowrap text-center">
                                                {userHourComments.length > 0 ? (
                                                    <div className="flex flex-col space-y-1">
                                                        <span className="tag tag-success !h-[20px] justify-center min-w-[28px] tabular-nums">
                                                            {activeComments.length}
                                                        </span>
                                                        <span className="tag tag-danger !h-[20px] justify-center min-w-[28px] tabular-nums">
                                                            {inactiveComments.length}
                                                        </span>
                                                    </div>
                                                ) : (
                                                    <div className="flex flex-col space-y-1">
                                                        <span className="text-gray-500 text-[13.5px]">—</span>
                                                        <span className="text-gray-500 text-[13.5px]">—</span>
                                                    </div>
                                                )}
                                            </Td>
                                        );
                                    }
                                    
                                    // Add total column
                                    cells.push(
                                        <Td key="total" className="px-1 py-4 whitespace-nowrap text-center bg-gray-50 dark:bg-night-raised">
                                            <div className="flex flex-col space-y-1">
                                                <span className="tag tag-success !h-[22px] justify-center min-w-[32px] tabular-nums">
                                                    {totalActiveComments}
                                                </span>
                                                <span className="tag tag-danger !h-[22px] justify-center min-w-[32px] tabular-nums">
                                                    {totalInactiveComments}
                                                </span>
                                            </div>
                                        </Td>
                                    );
                                    
                                    return cells;
                                })()}
                            </Tr>
                        ))}
                    </TBody>
                </Table>
            </div>
        </div>
    </Card>
)
    
}

export default CommentReportDaily