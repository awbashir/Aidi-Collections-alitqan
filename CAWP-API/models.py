"""SQLAlchemy models for the CAWP API (29 tables).

Moved verbatim out of app.py (which now does `from models import *`) so that the
blueprint modules can import them without importing the application object.

Deliberate changes made during the move:
  * `Clints.attribute` is `JSONB` (the Postgres column was migrated from json to jsonb);
    `Role.permission` and `Notification.data` stay `db.JSON` (still `json` in Postgres).
  * Every index that exists in the database is declared in `__table_args__` under its real
    name, so `flask db migrate` autogenerate does not try to drop/recreate them.
  * `__repr__` is uniform and always returns a string (several models used
    `__repr__ = as_dict`, which raises TypeError, or referenced a non-existent `self.name`).
  * `User.hash_password` (dead, called `.decode()` on a str) and `as_josn` (0 callers) removed.
Relationships are untouched on purpose: changing a `lazy=`/backref changes which object a
route sees (see the review, section 5 "what makes a refactor risky" #4).
"""
import json

from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from extensions import db

# Server default for `roles.permission`; also the canonical list of permission keys.

defult_permission = {
    "dashbord":False,
    "dashbord_admin":False,
    "dashbord_user":False,
    "branches":False,
    "branches_add":False,
    "branches_edit":False,
    "branches_delete":False,
    "nationalities":False,
    "nationalities_add":False,
    "nationalities_edit":False,
    "nationalities_delete":False,
    "roles":False,
    "roles_add":False,
    "roles_edit":False,
    "roles_delete":False,
    "users":False,
    "users_add":False,
    "users_edit":False,
    "users_delete":False,
    "users_details":False,
    "users_edit_general":False,
    "users_edit_target":False,
    "contracts":False,
    "contracts_add":False,
    "contracts_edit":False,
    "contracts_delete":False,
    "clints":False,
    "clints_add":False,
    "clints_edit":False,
    "clints_sepcifyUser":False,
    "clints_move_archive" : False,
    "clints_UpdateByExcel" : False,
    "clints_delete":False,
    "clints_callsRecords":False,
    "clints_callsRecords_download":False,
    "clints_callsRecords_play":False,
    "clints_details":False,
    "clints_comments":False,
    "clints_comments_delete":False,
    "clints_Payments":False,
    "clints_PayemntDates":False,
    "clints_contacts_delete":False,
    "clints_contacts_default":False,
    "client_query":False,
    "supervisors":False,
    "supervisors_add":False,
    "supervisors_edit":False,
    "supervisors_delete":False,
    "collections":False,
    "collections_currentUser":False,
    "collections_users":False,
    "reports":False,
    "reports_users":False,
    "report_users_comments" : False,
    "report_users_paymentDates" : False,
    "reports_clints" : False,
    "report_clintes_active" : False,
    "report_clintes_inactive" : False,
    "report_portfolios" : False,
    "report_quality_neglect" : False,
    "report_collectors" : False,
    "report_collectors_currentUser_paymentDates" : False,
    "report_collectors_currentUser_nextCalls" : False,
    "report_collectors_currentUser_comments" : False,
    "settings_general_archiveReasons" : False,
    "settings_general_archiveReasons_add" : False,
    "settings_general_archiveReasons_edit" : False,
    "settings_general_archiveReasons_delete" : False,
    "settings_general_delinquencyReasons_" : False,
    "settings_general_delinquencyReasons" : False,
    "settings_general_delinquencyReasons_add" : False,
    "settings_general_delinquencyReasons_edit" : False,
    "settings_general_delinquencyReasons_delete" : False,
    "settings_general_collectionMethods" : False,
    "settings_general_collectionMethods_add" : False,
    "settings_general_collectionMethods_edit" : False,
    "settings_general_collectionMethods_delete" : False,
    "settings_general_nationalities" : False,
    "settings_general_nationalities_add" : False,
    "settings_general_nationalities_edit" : False,
    "settings_general_nationalities_delete" : False,
    "settings_general_updateSide" : False,
    "settings_general_updateSide_add" : False,
    "settings_general_updateSide_edit" : False,
    "settings_general_updateSide_delete" : False,

    "clints_archive":False,
    "clints_archive_move":False,
    "portfolios":False,
    "portfolios_add":False,
    "portfolios_edit":False,
    "portfolios_delete":False,
    "portfolios_details":False,
    "settings":False,
    "settings_comments":False,
    "settings_comments_status" : False,
    "settings_comments_status_add":False,
    "settings_comments_status_edit":False,
    "settings_comments_status_delete":False,
    "accounts" : False,
    "accounts_payments" : False,
    "accounts_payments_edit" : False,
    "accounts_payments_delete" : False,
    "clints_lock": False,
    "clints_unlock": False,
    "clints_lock_history": False,
    "clints_bulk_lock": False,
    "clints_documents": False,
    "clints_documents_add": False,
    "clints_documents_delete": False,
    






}


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    email = db.Column(db.Text(), nullable=False)
    frist_name = db.Column(db.Text(), nullable=False)
    last_name = db.Column(db.Text(), nullable=False)
    mobile = db.Column(db.Text(), nullable=False)
    password = db.Column(db.Text(), nullable=False)
    user_name = db.Column(db.Text(), nullable=False , unique=True)
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    role_id = db.Column(UUID(as_uuid=True), db.ForeignKey('roles.id'), nullable=False)
    contract_id = db.Column(UUID(as_uuid=True), db.ForeignKey('contracts.id'), nullable=True)
    extension = db.Column(db.Text(), nullable=True)
    target_is_percentage = db.Column(db.Boolean(), nullable=True, server_default='false')
    target_amount = db.Column(db.Float(), nullable=True, server_default='0')
    target_percent = db.Column(db.Float(), nullable=True, server_default='0')
    nationality_id = db.Column(UUID(as_uuid=True), db.ForeignKey('nationalities.id'), nullable=False)
    branch_id = db.Column(UUID(as_uuid=True), db.ForeignKey('branches.id'), nullable=False)
    identityNumber = db.Column(db.Text(), nullable=True)

    __table_args__ = (
    db.Index('ix_users_role_id', 'role_id'),
    db.Index('ix_users_contract_id', 'contract_id'),
    db.Index('ix_users_nationality_id', 'nationality_id'),
    db.Index('ix_users_branch_id', 'branch_id'),
    )

    


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
    
    
    
    def get_supervisor(self):
        supervisorUsers = SupervisorUser.query.filter_by(user_id=self.id).all()
        self.SupervisorUser = []
        for supervisorUser in supervisorUsers:
            self.SupervisorUser.append(supervisorUser.as_dict())

        for supervisorUser in self.SupervisorUser:
            supervisorUser['supervisor'] = db.session.get(Supervisor, supervisorUser['supervisor_id']).as_dict()
            supervisorUser['supervisor']['user'] = db.session.get(User, supervisorUser['supervisor']['user_id']).as_dict()
            supervisorUser['supervisor']['user']['name'] = supervisorUser['supervisor']['user']['frist_name'] + " " + supervisorUser['supervisor']['user']['last_name']
        return self.SupervisorUser
    
    def get_countClints(self):
        return Clints.query.filter_by(user_id=self.id).count()
    
    def get_countClintsArchive(self):
        return Clints.query.filter_by(user_id=self.id,is_archive=True).count()
    
    def get_countClintsNotArchive(self):
        return Clints.query.filter_by(user_id=self.id,is_archive=False).count()
    
    
    def get_totalAmount(self):
        return db.session.query(func.sum(Clints.amount)).filter_by(user_id=self.id).scalar() or 0

    def get_totalAmountArchive(self):
        return db.session.query(func.sum(Clints.amount)).filter_by(user_id=self.id,is_archive=True).scalar() or 0
    
    def get_totalAmountNotArchive(self):
        return db.session.query(func.sum(Clints.amount)).filter_by(user_id=self.id,is_archive=False).scalar() or 0

    def __repr__(self):
        return f"<User {self.id}>"



class Role(db.Model):
    __tablename__ = 'roles'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    permission = db.Column(db.JSON(), nullable=False , server_default=json.dumps(defult_permission))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    users = db.relationship('User', backref='role', lazy=True)

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Role {self.id}>"



class Nationality(db.Model):
    __tablename__ = 'nationalities'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
   


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Nationality {self.id}>"



class UpdateSides(db.Model):
    __tablename__ = 'updatesides'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
   


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<UpdateSides {self.id}>"



class Branch(db.Model):
    __tablename__ = 'branches'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    users = db.relationship('User', backref='branch', lazy=True)

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Branch {self.id}>"



class Contract(db.Model):
    __tablename__ = 'contracts'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    monthly_target_percent = db.Column(db.Float(), nullable=True, server_default='0')
    monthly_target_amount = db.Column(db.Float(), nullable=True, server_default='0')
    monthly_target_is_percentage = db.Column(db.Boolean(), nullable=True, server_default='false')

    def get_countClints(self):
        return db.session.query(func.count(Clints.id)).join(Portfolios, Portfolios.id == Clints.portfolios_id).filter(Portfolios.contract_id == self.id).scalar() or 0
    
    def get_totalAmount(self):
        return db.session.query(func.sum(Clints.amount)).join(Portfolios, Portfolios.id == Clints.portfolios_id).filter(Portfolios.contract_id == self.id).scalar() or 0
    
    def get_totalAmountArchive(self):
        return db.session.query(func.sum(Clints.amount)).join(Portfolios, Portfolios.id == Clints.portfolios_id).filter(Portfolios.contract_id == self.id,Clints.is_archive==True).scalar() or 0
    
    def get_totalAmountNotArchive(self):
        return db.session.query(func.sum(Clints.amount)).join(Portfolios, Portfolios.id == Clints.portfolios_id).filter(Portfolios.contract_id == self.id,Clints.is_archive==False).scalar() or 0
    
    def get_countClintsArchive(self):
        return db.session.query(func.count(Clints.id)).join(Portfolios, Portfolios.id == Clints.portfolios_id).filter(Portfolios.contract_id == self.id,Clints.is_archive==True).scalar() or 0
    
    def get_countClintsNotArchive(self):
        return  db.session.query(func.count(Clints.id)).join(Portfolios, Portfolios.id == Clints.portfolios_id).filter(Portfolios.contract_id == self.id,Clints.is_archive==False).scalar() or 0
    

   


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Contract {self.id}>"



class Supervisor(db.Model):
    __tablename__ = 'supervisors'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    contracts = db.relationship('Contract', secondary='supervisor_contracts', lazy='select', backref=db.backref('supervisors', lazy=True))
    users = db.relationship('User', secondary='supervisor_users', lazy='select', backref=db.backref('supervisors', lazy=True))

    __table_args__ = (
    db.Index('ix_supervisors_user_id', 'user_id'),
    )


   


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Supervisor {self.id}>"



class SupervisorContract(db.Model):
    __tablename__ = 'supervisor_contracts'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    supervisor_id = db.Column(UUID(as_uuid=True), db.ForeignKey('supervisors.id'), nullable=False)
    contract_id = db.Column(UUID(as_uuid=True), db.ForeignKey('contracts.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')

    __table_args__ = (
    db.Index('ix_supcontracts_sup_id', 'supervisor_id'),
    db.Index('ix_supcontracts_sup_contract', 'supervisor_id', 'contract_id'),
    db.Index('ix_supervisor_contracts_contract_id', 'contract_id'),
    )


   


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<SupervisorContract {self.id}>"



class SupervisorUser(db.Model):
    __tablename__ = 'supervisor_users'

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    supervisor_id = db.Column(UUID(as_uuid=True), db.ForeignKey('supervisors.id'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')

    __table_args__ = (
    db.Index('ix_supusers_sup_user', 'supervisor_id', 'user_id'),
    db.Index('ix_supusers_sup_id', 'supervisor_id'),
    db.Index('ix_supervisor_users_user_id', 'user_id'),
    )



    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<SupervisorUser {self.id}>"



class Clints(db.Model):
    __tablename__ = 'clints'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    identityNumber = db.Column(db.Text(), nullable=False) 
    accountNumber = db.Column(db.Text(), unique=True,nullable=False)
    amount = db.Column(db.Float(), nullable=False)
    attribute = db.Column(JSONB(), nullable=False)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    #sepcifyUser_id = db.Column(UUID(as_uuid=True), db.ForeignKey('SepcifyUsers.id'), nullable=True)
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    is_archive = db.Column(db.Boolean(), nullable=False, server_default='false')
    is_locked = db.Column(db.Boolean(), nullable=False, server_default='false')
    clintArchive_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clintArchive.id'), nullable=True)
    portfolios_id = db.Column(UUID(as_uuid=True), db.ForeignKey('portfolios.id',ondelete='CASCADE'), nullable=False)
    delinquencyReason_id = db.Column(UUID(as_uuid=True), db.ForeignKey('delinquencyReason.id'), nullable=True)
    # Denormalised "latest follow-up", maintained by the `comments_last_comment_trg`
    # trigger on "Comments" (db/03_last_comment.sql). The grids and the neglect report
    # order/filter on `last_comment_at` and join the comment by `last_comment_id`
    # instead of running a LATERAL over 1.27M rows per request.
    last_comment_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_comment_id = db.Column(UUID(as_uuid=True), nullable=True)

    payments = db.relationship('Payments', backref='clint', lazy=True,cascade="all, delete-orphan")
    comments = db.relationship('Comments', backref='clint', lazy=True,cascade="all, delete-orphan") 
    paymentDates = db.relationship('PaymentDates', backref='clint', lazy=True,cascade="all, delete-orphan")
    sepcifyUsers = db.relationship('SepcifyUsers', backref='clint', lazy=True,cascade="all, delete-orphan")
    users = db.relationship('User', backref='clint', lazy=True)
    portfolios = db.relationship('Portfolios', backref='clint', lazy=True)
    clintArchive = db.relationship('ClintArchive', backref='clint', lazy=True ,foreign_keys=[clintArchive_id])
    delinquencyReasons = db.relationship('DelinquencyReason', backref='clint', lazy=True)

    __table_args__ = (
    db.Index('ix_clints_user_id', 'user_id'),
    db.Index('ix_clints_user_notarch', 'user_id',
             postgresql_where=db.text('is_archive = false')),
    db.Index('ix_clints_archive_id', 'clintArchive_id'),
    db.Index('ix_clints_create_at', create_at.desc()),
    db.Index('ix_clints_portfolios_id', 'portfolios_id'),
    db.Index('ix_clints_portfolios_notarch', 'portfolios_id',
             postgresql_where=db.text('is_archive = false')),
    db.Index('ix_clints_identity', 'identityNumber'),
    db.Index('ix_clints_name_trgm', 'name',
             postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'}),
    db.Index('ix_clints_identity_trgm', 'identityNumber',
             postgresql_using='gin', postgresql_ops={'identityNumber': 'gin_trgm_ops'}),
    db.Index('ix_clints_delinquency', 'delinquencyReason_id',
             postgresql_where=db.text('"delinquencyReason_id" IS NOT NULL')),
    db.Index('ix_clints_archive_create', create_at.desc(),
             postgresql_where=db.text('is_archive = true')),
    db.Index('ix_clints_live_create', create_at.desc(),
             postgresql_where=db.text('is_archive = false')),
    db.Index('ix_clints_last_comment_live', last_comment_at.desc().nullslast(),
             postgresql_where=db.text('is_archive = false')),
    db.Index('ix_clints_user_last_comment_live', 'user_id',
             last_comment_at.desc().nullslast(),
             postgresql_where=db.text('is_archive = false')),
    db.Index('ix_clints_last_comment_live_asc', last_comment_at.asc().nullslast(),
             postgresql_where=db.text('is_archive = false')),
    db.Index('ix_clints_last_comment_id', 'last_comment_id',
             postgresql_where=db.text('last_comment_id IS NOT NULL')),
    )


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Clints {self.id}>"



class ClientLockHistory(db.Model):
    __tablename__ = 'client_lock_history'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.Text(), nullable=False)  # 'lock' or 'unlock'
    reason = db.Column(db.Text(), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    
    # Relationships
    clint = db.relationship('Clints', backref='lock_history', lazy=True)
    user = db.relationship('User', backref='client_locks', lazy=True)

    __table_args__ = (
    db.Index('ix_lockhistory_clint_created', 'clint_id', created_at.desc()),
    db.Index('ix_lockhistory_user', 'user_id'),
    )


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ClientLockHistory {self.id}>"



class CommentStatuses(db.Model):
    __tablename__ = 'CommentStatuses'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    contract_id = db.Column(UUID(as_uuid=True), db.ForeignKey('contracts.id'), nullable=True)
    CommentReachability_id = db.Column(UUID(as_uuid=True), db.ForeignKey('comment_reachability.id'), nullable=True) 
    color = db.Column(db.Text(), nullable=True)
    is_active = db.Column(db.Boolean(), nullable=True, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
    db.Index('ix_commentstatuses_contract_id', 'contract_id'),
    db.Index('ix_commentstatuses_reachability', 'CommentReachability_id'),
    )

   


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<CommentStatuses {self.id}>"



class Comments(db.Model):
    __tablename__ = 'Comments'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)  
    contactMethod_id = db.Column(UUID(as_uuid=True), db.ForeignKey('ContactMethods.id'), nullable=False)    
    comment = db.Column(db.Text(), nullable=False)
    comment_status_id = db.Column(UUID(as_uuid=True), db.ForeignKey('CommentStatuses.id'), nullable=False)
    comment_reachability_id = db.Column(UUID(as_uuid=True), db.ForeignKey('comment_reachability.id'), nullable=True)
    call_id = db.Column(db.Text(), nullable=True)
    next_contact_date = db.Column(db.DateTime(timezone=True), nullable=True)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    is_archive = db.Column(db.Boolean(), nullable=False, server_default='false')
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    contactNumber = db.Column(db.Text(), nullable=True)
    # `Comments.clint` / `Clints.comments` (declared on Clints) already map this FK;
    # `overlaps=` tells the mapper that is intentional instead of emitting a SAWarning.
    clints = db.relationship('Clints', lazy=True, overlaps="clint,comments",
                             backref=db.backref('clint_comments', lazy=True,
                                                overlaps="clint,comments"))
    commentStatuses = db.relationship('CommentStatuses', backref='status_comments', lazy=True)
     # Add the missing relationships
    user = db.relationship('User', backref='user_comments', lazy=True)
    contactMethod = db.relationship('ContactMethods', backref='method_comments', lazy=True)

    __table_args__ = (
    db.Index('ix_comments_user_id', 'user_id'),
    db.Index('ix_comments_clint_id', 'clint_id'),
    db.Index('ix_comments_status', 'comment_status_id'),
    db.Index('ix_comments_user_created', 'user_id', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_comments_clint_created', 'clint_id', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_comments_create_at', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_comments_next_contact', 'user_id', 'next_contact_date',
             postgresql_where=db.text('next_contact_date IS NOT NULL AND is_deleted = false')),
    )



    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Comments {self.id}>"



class ClintContacts(db.Model):
    __tablename__ = 'ClintContacts'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    contact_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey('ClintContactTypes.id'), nullable=False)
    identityNumber = db.Column(db.Text(), nullable=False)
    contact_status = db.Column(db.Text(), nullable=False)
    contact = db.Column(db.Text(), nullable=False)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_active = db.Column(db.Boolean(), nullable=False, server_default='true')
    is_archive = db.Column(db.Boolean(), nullable=False, server_default='false')
    is_default = db.Column(db.Boolean(), nullable=False, server_default='false')
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')

    __table_args__ = (
    db.Index('ix_clintcontacts_identity', 'identityNumber'),
    db.Index('ix_clintcontacts_identity_default', 'identityNumber',
             postgresql_where=db.text('is_default = true AND is_deleted = false')),
    )

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ClintContacts {self.id}>"



class ClintContactTypes(db.Model):
    __tablename__ = 'ClintContactTypes'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ClintContactTypes {self.id}>"



class CollectionTypes(db.Model):
    __tablename__ = 'CollectionTypes'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<CollectionTypes {self.id}>"



class CollectionMethods(db.Model):

    __tablename__ = 'CollectionMethods'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<CollectionMethods {self.id}>"



class Payments(db.Model):
    __tablename__ = 'Payments'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id' , ondelete='CASCADE'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    collection_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey('CollectionTypes.id'), nullable=False)
    collection_method_id = db.Column(UUID(as_uuid=True), db.ForeignKey('CollectionMethods.id'), nullable=False)
    amount = db.Column(db.Float(), nullable=False)
    payment_date = db.Column(db.DateTime(timezone=True), nullable=False)
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    is_archive = db.Column(db.Boolean(), nullable=False, server_default='false')
    approval_status = db.Column(db.Boolean(), nullable=True)
    approval_by = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    approval_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approval_comment = db.Column(db.Text(), nullable=True)

    __table_args__ = (
    db.Index('ix_payments_clint_id', 'clint_id'),
    db.Index('ix_payments_user_id', 'user_id'),
    db.Index('ix_payments_collection_type', 'collection_type_id'),
    db.Index('ix_payments_collection_method', 'collection_method_id'),
    db.Index('ix_payments_create_at', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_payments_user_created', 'user_id', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_payments_payment_date', 'payment_date',
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_payments_approval_by', 'approval_by',
             postgresql_where=db.text('approval_by IS NOT NULL')),
    db.Index('ix_payments_approval_status', 'approval_status', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    )





    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Payments {self.id}>"



class ContactMethods(db.Model):
    __tablename__ = 'ContactMethods'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ContactMethods {self.id}>"



class PaymentDates(db.Model):
    __tablename__ = 'paymentDates'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    payment_date = db.Column(db.DateTime(timezone=True), nullable=False)
    amount = db.Column(db.Float(), nullable=False)
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    is_archive = db.Column(db.Boolean(), nullable=False, server_default='false')

    __table_args__ = (
    db.Index('ix_paymentdates_clint_id', 'clint_id'),
    db.Index('ix_paymentdates_user_id', 'user_id'),
    db.Index('ix_paymentdates_user_date', 'user_id', 'payment_date',
             postgresql_where=db.text('is_deleted = false')),
    db.Index('ix_paymentdates_payment_date', 'payment_date',
             postgresql_where=db.text('is_deleted = false')),
    )





    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<PaymentDates {self.id}>"



class ClintArchive(db.Model):
    __tablename__ = 'clintArchive'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    archive_reason_id = db.Column(UUID(as_uuid=True), db.ForeignKey('archiveReason.id'), nullable=False)
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    users = db.relationship('User', backref='clintArchive', lazy=True)
    archiveReasons = db.relationship('ArchiveReason', backref='clintArchive', lazy=True)

    __table_args__ = (
    db.Index('ix_clintarchive_clint_id', 'clint_id'),
    db.Index('ix_clintarchive_user_id', 'user_id'),
    db.Index('ix_clintarchive_reason', 'archive_reason_id'),
    db.Index('ix_clintarchive_create', create_at.desc()),
    )


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ClintArchive {self.id}>"



class ClintDocuments(db.Model):
    __tablename__ = 'clint_documents'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id', ondelete='CASCADE'), nullable=False)
    file_name = db.Column(db.Text(), nullable=False)
    original_name = db.Column(db.Text(), nullable=False)
    display_name = db.Column(db.Text(), nullable=True) 
    file_type = db.Column(db.Text(), nullable=False)
    uploaded_by = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    clint = db.relationship('Clints', backref=db.backref('documents', lazy=True, cascade="all, delete-orphan"))
    user = db.relationship('User', backref='clintDocuments', lazy=True)

    __table_args__ = (
    db.Index('ix_clint_documents_clint_id', 'clint_id'),
    )


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ClintDocuments {self.id}>"



class DelinquencyReason(db.Model):
    __tablename__ = 'delinquencyReason'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<DelinquencyReason {self.id}>"



class ArchiveReason(db.Model):
    __tablename__ = 'archiveReason'
    id  = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)




    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<ArchiveReason {self.id}>"



class Portfolios(db.Model):
    __tablename__ = 'portfolios'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    contract_id = db.Column(UUID(as_uuid=True), db.ForeignKey('contracts.id'), nullable=False)
    # same FK as `Clints.portfolios` / `Portfolios.clint` - see the note on Comments.clints
    clints = db.relationship('Clints', lazy=True, cascade="delete",
                             overlaps="clint,portfolios",
                             backref=db.backref('portfolio', lazy=True,
                                                overlaps="clint,portfolios"))

    contracts = db.relationship('Contract', backref='portfolio', lazy=True)

    __table_args__ = (
    db.Index('ix_portfolios_contract_id', 'contract_id'),
    )




    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Portfolios {self.id}>"



class SepcifyUsers(db.Model):
    __tablename__ = 'SepcifyUsers'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    clint_id = db.Column(UUID(as_uuid=True), db.ForeignKey('clints.id' , ondelete='CASCADE'),  nullable=False)
    create_by = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    update_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
    db.Index('ix_sepcifyusers_user_clint', 'user_id', 'clint_id'),
    db.Index('ix_specifyusers_clint_notdel', 'clint_id', 'is_deleted'),
    db.Index('ix_specify_clint_created', 'clint_id', create_at.desc(),
             postgresql_where=db.text('is_deleted = false')),
    )




    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<SepcifyUsers {self.id}>"



class CommentReachability(db.Model):
    __tablename__ = 'comment_reachability'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=db.text('gen_random_uuid()'))
    name = db.Column(db.Text(), nullable=False)
    is_deleted = db.Column(db.Boolean(), nullable=False, server_default='false')
    create_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())



    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<CommentReachability {self.id}>"



class LoginLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    ip_address = db.Column(db.String(100))
    outcome = db.Column(db.String(50))  # e.g., "success", "failure", "user not active", "user not found", etc.

    __table_args__ = (
    db.Index('ix_loginlog_username_ts', 'username', timestamp.desc()),
    )


    
    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
    
#Add this model to your models section

    def __repr__(self):
        return f"<LoginLog {self.id}>"



class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.Text, nullable=False)
    data = db.Column(db.JSON, nullable=True)
    sender_user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    receiver_user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
    db.Index('ix_notifications_sender', 'sender_user_id'),
    db.Index('ix_notifications_receiver_created', 'receiver_user_id', created_at.desc()),
    db.Index('ix_notifications_receiver_unread', 'receiver_user_id',
             postgresql_where=db.text('is_read IS NOT TRUE')),
    )


    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self):
        return f"<Notification {self.id}>"




__all__ = [
    "db",
    "defult_permission",
    "User",
    "Role",
    "Nationality",
    "UpdateSides",
    "Branch",
    "Contract",
    "Supervisor",
    "SupervisorContract",
    "SupervisorUser",
    "Clints",
    "ClientLockHistory",
    "CommentStatuses",
    "Comments",
    "ClintContacts",
    "ClintContactTypes",
    "CollectionTypes",
    "CollectionMethods",
    "Payments",
    "ContactMethods",
    "PaymentDates",
    "ClintArchive",
    "ClintDocuments",
    "DelinquencyReason",
    "ArchiveReason",
    "Portfolios",
    "SepcifyUsers",
    "CommentReachability",
    "LoginLog",
    "Notification",
]
